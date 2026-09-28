"""Replay a successful local setup using freshly upgraded Basaltwater code."""

from __future__ import annotations

import argparse
from dataclasses import replace
import os
from pathlib import Path
import pwd
import shlex
import stat
import sys
from datetime import datetime, timezone

from lib.arg_parser import add_setup_arguments
from lib.atomic_io import read_json_file, write_json_atomic
from lib.cachyos import _OPTIONS, cachyos_config_from_args, is_cachyos, preflight_cachyos, validate_cachyos_config
from lib.channel_manager import ChannelError, get_channel_info, managed_repository_path, upgrade_channel
from lib.config import AGENT_TOOLS, SetupConfig
from lib.remote_utils import is_dry_run
from lib.validation import validate_filesystem_path


class _SavedSetupParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ValueError(f"Invalid saved CachyOS setup: {message}")


class _RefreshOption(argparse.Action):
    """Retain explicit setup tokens, without introducing setup defaults."""

    def __call__(self, parser, namespace, values, option_string=None):
        overrides = dict(getattr(namespace, "setup_overrides", None) or {})
        tokens = list(overrides.get(self.dest, []))
        tokens.append(option_string)
        if isinstance(values, list):
            tokens.extend(values)
        elif values is not None:
            tokens.append(values)
        overrides[self.dest] = tokens
        namespace.setup_overrides = overrides


def add_refresh_arguments(parser: argparse.ArgumentParser, setup_parser: argparse.ArgumentParser) -> None:
    """Expose only supported local options, using setup's argument shapes."""
    for action in setup_parser._actions:
        if not action.option_strings or action.dest not in _OPTIONS - {"dry_run", "host", "username", "system_type"}:
            continue
        parser.add_argument(*action.option_strings, dest=action.dest, action=_RefreshOption,
                            nargs=action.nargs, default=argparse.SUPPRESS,
                            metavar=action.metavar, help="CachyOS refresh: " + (action.help or ""))


def _replay_arguments(config: SetupConfig) -> list[str]:
    selected = config.selected_agent_tools()
    replay = replace(config, agent_tools=selected,
                     agent_tools_removed=[tool for tool in AGENT_TOOLS if tool not in selected])
    arguments = shlex.split(" ".join(replay.to_setup_command()))[1:]
    existing_options = set(zip(arguments, arguments[1:]))
    for tool in AGENT_TOOLS:
        option = "--agent-tool" if tool in selected else "--no-agent-tool"
        if (option, tool) not in existing_options:
            arguments.extend([option, tool])
    return arguments


def _merge_overrides(arguments: list[str], overrides: dict[str, list[str]]) -> tuple[list[str], SetupConfig]:
    parser = _SavedSetupParser(add_help=False, allow_abbrev=False)
    add_setup_arguments(parser, allow_steps=True, include_system_type=True)
    saved = parser.parse_args(arguments[1:])
    tokens = [token for values in overrides.values() for token in values]
    additions = parser.parse_args(["agent_cachyos", "localhost", _account().pw_name, *tokens])
    if set(additions.agent_tools or []) & set(additions.no_agent_tools or []):
        raise ValueError("Do not both select and exclude the same agent in refresh")
    if "t3code_desktop" in overrides and {"web_interface_host", "web_interface_port"} & overrides.keys():
        raise ValueError("Web bind/port options cannot be combined with --t3code-desktop")
    for name in overrides:
        if name not in _OPTIONS - {"dry_run", "host", "username", "system_type"}:
            raise ValueError(f"Unsupported refresh override: {name}")
        value = getattr(additions, name)
        if isinstance(value, list):
            value = list(dict.fromkeys([*(getattr(saved, name) or []), *value]))
        setattr(saved, name, value)
    # Saved provider exclusions are explicit. A new selection must override
    # its old opposite, without losing exclusions for the other providers.
    for name, opposite in (("agent_tools", "no_agent_tools"), ("no_agent_tools", "agent_tools")):
        if name in overrides:
            setattr(saved, opposite, [tool for tool in getattr(saved, opposite) or []
                                      if tool not in (getattr(additions, name) or [])])
    if "t3code_desktop" in overrides:
        saved.web_interfaces = None
        saved.web_interface_host = None
        saved.web_interface_port = parser.get_default("web_interface_port")
    elif "web_interfaces" in overrides:
        saved.t3code_desktop = False
    if getattr(additions, "clear_access_sources", False):
        saved.access_sources = additions.access_sources
    elif "access_sources" in overrides:
        saved.clear_access_sources = False
    config = cachyos_config_from_args(saved)
    arguments = _replay_arguments(config)
    return arguments, _parse_saved_arguments(arguments)


def _account() -> pwd.struct_passwd:
    if os.geteuid() == 0 or os.geteuid() != os.getuid():
        raise ValueError("Run refresh/setup as the desktop user, without sudo")
    return pwd.getpwuid(os.getuid())


def _record_path() -> Path:
    home = Path(_account().pw_dir)
    path = home / ".local/state/basaltwater/cachyos/last-setup.json"
    validate_filesystem_path(str(path))
    for parent in (path.parent, *path.parent.parents):
        if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
            raise ValueError(f"Unsafe saved setup directory: {parent}")
        if parent.is_relative_to(home) and parent.exists():
            info = parent.stat()
            if info.st_uid != os.getuid() or info.st_mode & 0o022:
                raise ValueError(f"Saved setup directory must be owned by you and not writable by others: {parent}")
    if path.exists() or path.is_symlink():
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077):
            raise ValueError("Saved setup must be a private regular file owned by the desktop user")
    return path


def _parse_saved_arguments(arguments: object) -> SetupConfig:
    if (not isinstance(arguments, list) or not 3 <= len(arguments) <= 512
            or any(not isinstance(value, str) or not value or len(value) > 4096
                   or any(ord(char) < 32 or ord(char) == 127 for char in value)
                   for value in arguments)
            or arguments[:2] != ["setup", "agent_cachyos"]):
        raise ValueError("Invalid saved CachyOS setup arguments")
    parser = _SavedSetupParser(add_help=False, allow_abbrev=False)
    add_setup_arguments(parser, allow_steps=True, include_system_type=True)
    config = cachyos_config_from_args(parser.parse_args(arguments[1:]))
    if config.username != _account().pw_name or config.dry_run:
        raise ValueError("Saved setup must belong to this desktop user and must not be a dry run")
    return config


def save_successful_setup(config: SetupConfig) -> None:
    """Final plugin step: failed runs and previews never replace the selection."""
    if config.dry_run or is_dry_run():
        return
    validate_cachyos_config(config)
    # Make provider exclusions explicit so a profile-default change cannot
    # silently opt the user into another currently supported coding agent.
    arguments = _replay_arguments(config)
    _parse_saved_arguments(arguments)
    path = _record_path()
    home = Path(_account().pw_dir)
    for parent in reversed(path.parent.parents):
        if parent == home or parent.is_relative_to(home):
            parent.mkdir(mode=0o700, exist_ok=True)
    path.parent.mkdir(mode=0o700, exist_ok=True)
    _record_path()
    write_json_atomic(str(path), {"schema_version": 1, "arguments": arguments}, mode=0o600)
    # Keep the replay contract compatible with older launchers. The private
    # receipt is informational and never used as executable setup arguments.
    from lib.cachyos_doctor import collect_cachyos_doctor, _probe
    from lib.cachyos_health import source_metadata

    try:
        report = collect_cachyos_doctor(config=config)
        receipt = {
            "schema_version": 1,
            "completed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source": source_metadata(_probe, os.getuid()),
            "arguments": arguments,
            "observations": report["capabilities"],
        }
        receipt_path = path.with_name("last-report.json")
        if receipt_path.is_symlink() or (receipt_path.exists() and not receipt_path.is_file()):
            raise ValueError("Unsafe setup receipt")
        write_json_atomic(str(receipt_path), receipt, mode=0o600)
    except (OSError, ValueError, KeyError):
        print("  WARNING: Setup saved, but its diagnostic receipt could not be recorded")
    print("  Saved successful local setup; use `basaltw refresh` to upgrade and repeat it")


def load_saved_setup() -> tuple[list[str], SetupConfig]:
    path = _record_path()
    if not path.exists():
        raise ValueError("No successful CachyOS setup has been saved. Run your usual "
                         "`basaltw setup agent_cachyos localhost ...` command once first; "
                         "older runs cannot be reconstructed automatically.")
    record = read_json_file(str(path), max_bytes=65536)
    if (not isinstance(record, dict) or set(record) != {"schema_version", "arguments"}
            or type(record["schema_version"]) is not int or record["schema_version"] != 1):
        raise ValueError("Unsupported saved CachyOS setup format; rerun setup with your desired flags")
    arguments = record["arguments"]
    config = _parse_saved_arguments(arguments)
    return arguments, config


def run_refresh_command(args: argparse.Namespace) -> int:
    """Upgrade once, then exec the new CLI rather than using already loaded code."""
    try:
        if not is_cachyos():
            raise ValueError("refresh currently supports only a saved local agent_cachyos setup")
        arguments, config = load_saved_setup()
        overrides = getattr(args, "setup_overrides", None)
        if overrides:
            arguments, config = _merge_overrides(arguments, overrides)
        repository = managed_repository_path()
        if args.dry_run:
            info = get_channel_info(repository)
            if not info.get("channel") or info.get("installation_type") == "setup-snapshot":
                raise ValueError("refresh requires an installation with a managed upgrade channel")
            print(f"[DRY-RUN] Would upgrade Basaltwater on channel {info['channel']}; no fetch or checkout")
            print("Saved setup: " + shlex.join(["basaltw", *arguments]))
            from remote_setup import run_cachyos_setup

            return run_cachyos_setup(replace(config, dry_run=True))
        preflight_cachyos(config)
        info = upgrade_channel(repository)
        print(f"Basaltwater is at {str(info['commit'])[:12]} on {info['channel']}")
        print("Repeating the saved setup with the updated code:", flush=True)
        print(shlex.join(["basaltw", *arguments]), flush=True)
        # Never resolve basaltw through PATH: it could select a different copy.
        # exec preserves the terminal for sudo/AUR prompts and the setup exit code.
        entry = str(Path(repository) / "basaltwater.py")
        os.execv(sys.executable, [sys.executable, entry, *arguments])
        return 0
    except (ChannelError, OSError, ValueError, RuntimeError) as exc:
        print(f"Error: {exc}")
        return 1
