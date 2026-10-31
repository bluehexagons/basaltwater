"""Upgrade the active source and replay this machine's successful setup."""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import pwd
import shlex
import shutil
import stat
import sys
import tempfile
from urllib.parse import urlsplit

from lib.arg_parser import create_setup_argument_parser
from lib.admin_status import JOB_ID_ENV, valid_job_id
from lib.atomic_io import read_json_file, write_json_atomic, write_text_atomic
from lib.cachyos import is_cachyos
from lib.channel_manager import get_channel_info, managed_repository_path, switch_channel, upgrade_channel
from lib.installation_info import INSTALLATION_METADATA_FILENAME, REFRESH_METADATA_FILENAME, write_refresh_source_metadata
from lib.config import AGENT_TOOLS, SetupConfig
from lib.machine_state import (
    PERSISTENT_SETUP_CONFIG_FILE as SETUP_CONFIG_FILE,
    PERSISTENT_STATE_DIR as STATE_DIR,
    _validate_setup_config,
    can_manage_system_services,
)
from lib.maintenance_lock import maintenance_lock
from lib.operation_state import OperationRecord, OperationStateStore
from lib.remote_utils import read_os_release, run
from lib.validation import validate_channel, validate_filesystem_path


_SOURCE_URL = "https://github.com/bluehexagons/basaltwater.git"
SOURCE_ACTIVATION_MARKER_FILENAME = ".basaltwater-source-refresh-operation.json"
_SNAPSHOT_SOURCE_NAMES = {
    "basaltwater.py", "remote_setup.py", "pyproject.toml", "lib", "plugins", "game", "desktop",
    "web", "smb", "security", "sync", "common", "deploy", INSTALLATION_METADATA_FILENAME,
    REFRESH_METADATA_FILENAME,
}


def saved_setup_revision(path: str | None = None) -> str:
    """Bind a refresh replay to protected local setup metadata, without secrets."""
    from lib.privilege_policy import protected_path

    path = SETUP_CONFIG_FILE if path is None else path
    validate_filesystem_path(path, must_exist=True)
    protected_path(path)
    metadata = os.stat(path, follow_symlinks=False)
    identity = (metadata.st_dev, metadata.st_ino, metadata.st_size,
                metadata.st_mtime_ns, metadata.st_ctime_ns, metadata.st_mode, metadata.st_uid)
    return hashlib.sha256(repr(identity).encode("ascii")).hexdigest()


def _protected_operation_record(path: str) -> OperationRecord | None:
    """Load a durable marker only after protecting its directory and leaf."""
    from lib.privilege_policy import protected_path

    parent = os.path.dirname(path)
    protected_path(parent, directory=True)
    for candidate in (path, path + ".lock"):
        try:
            os.lstat(candidate)
        except FileNotFoundError:
            continue
        protected_path(candidate)
    return OperationStateStore(path).load()


def _source_activation_marker(repository: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(repository)), SOURCE_ACTIVATION_MARKER_FILENAME)


def _assert_no_source_activation(repository: str) -> None:
    marker = _source_activation_marker(repository)
    record = _protected_operation_record(marker)
    if record is not None:
        raise RuntimeError(
            f"Unresolved Basaltwater source activation at {marker} "
            f"(operation {record.operation_id}, phase {record.phase}); inspect its recorded paths before retrying"
        )


def _setup_operation_marker() -> str:
    return os.path.join(os.path.realpath(STATE_DIR), "setup-operation.json")


def _assert_setup_operation_ready(config: SetupConfig) -> None:
    """Refuse refresh before source changes if setup stopped without recovery details."""
    marker = _setup_operation_marker()
    record = _protected_operation_record(marker)
    if record is None:
        return
    context = record.context
    matching_recovery = (
        record.operation_type == "target_setup"
        and record.resource == config.system_type
        and record.status == "recovery_required"
        and context.get("machine_type") == config.machine_type
        and context.get("system_type") == config.system_type
        and context.get("username") == config.username
    )
    if matching_recovery:
        return
    if record.operation_type == "target_setup" and record.status == "in_progress":
        reason = "an interrupted target setup has no recorded recovery decision"
    else:
        reason = "an unfinished or mismatched target setup operation needs inspection"
    raise RuntimeError(
        f"Refresh stopped before changing source because {reason} at {marker}; "
        "inspect the marker and target state before retrying"
    )


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


def _normalize_downloaded_source(staged: str, *, runtime_only: bool = False) -> None:
    """Make code readable under private job umasks without touching runtime data."""
    def walk_error(error):
        raise error

    for directory, directories, files in os.walk(staged, onerror=walk_error):
        if directory == staged:
            if runtime_only:
                directories[:] = [name for name in directories if name in _SNAPSHOT_SOURCE_NAMES]
                files = [name for name in files if name in _SNAPSHOT_SOURCE_NAMES]
        os.chmod(directory, 0o755)
        for name in (*directories, *files):
            path = os.path.join(directory, name)
            mode = os.lstat(path).st_mode
            if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                raise ValueError(f"Downloaded source must be regular files and directories: {path}")
            if stat.S_ISREG(mode):
                os.chmod(path, 0o755 if mode & 0o111 else 0o644)
        if directory == staged:
            directories[:] = [name for name in directories if name not in {".git", ".basaltwater"}]


def _upgrade_snapshot(repository: str, channel: str) -> dict[str, object]:
    """Stage a managed checkout and retain runtime data with rollback on activation."""
    from lib.privilege_policy import protected_path

    _assert_no_source_activation(repository)
    protected_path(repository, directory=True)
    protected_path(os.path.join(repository, INSTALLATION_METADATA_FILENAME))
    parent = os.path.dirname(repository)
    validate_filesystem_path(parent, must_exist=True)
    temporary = tempfile.mkdtemp(prefix=".basaltwater-refresh-", dir=parent)
    keep_staged = False
    operation_store: OperationStateStore | None = None
    operation_record: OperationRecord | None = None
    operation_resolved = False
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
        # Administrative jobs inherit a private umask. Normalize only the new
        # source, before retained state or deployment data enters the tree.
        _normalize_downloaded_source(staged)
        # The controller stages just these source entries. Preserve all other
        # top-level runtime data without copying potentially large deployments.
        data_names = sorted(set(os.listdir(repository)) - _SNAPSHOT_SOURCE_NAMES)
        for name in data_names:
            if os.path.lexists(os.path.join(staged, name)):
                raise ValueError(f"Downloaded source conflicts with retained runtime data: {name}")
        backup = tempfile.mkdtemp(prefix=".basaltwater-before-refresh-", dir=parent)
        os.rmdir(backup)
        marker = _source_activation_marker(repository)
        operation_store = OperationStateStore(marker)
        operation_record = operation_store.begin(
            "source_refresh",
            repository,
            "prepared",
            context={
                "repository_path": repository,
                "backup_path": backup,
                "staged_path": staged,
                "temporary_path": temporary,
                "runtime_data_names": data_names,
            },
        )
        moved = []
        renamed = False
        try:
            # The operation marker is durable before the installed source is
            # displaced. A hard kill at any later boundary leaves all planned
            # copy paths available for an administrator to inspect.
            os.rename(repository, backup)
            renamed = True
            operation_record = operation_store.transition(operation_record.operation_id, "source_displaced")
            for name in data_names:
                os.rename(os.path.join(backup, name), os.path.join(staged, name))
                moved.append(name)
            operation_record = operation_store.transition(operation_record.operation_id, "runtime_data_moved")
            os.chmod(staged, 0o755)
            operation_record = operation_store.transition(operation_record.operation_id, "activating_staged_source")
            os.rename(staged, repository)
        except Exception as error:
            # Asynchronous interruption can arrive after a rename took effect
            # but before the bookkeeping above ran. Leave its durable marker
            # and every copy intact instead of guessing which moves completed.
            try:
                for name in reversed(moved):
                    os.rename(os.path.join(staged, name), os.path.join(backup, name))
                if renamed:
                    os.rename(backup, repository)
            except OSError as recovery_error:
                operation_store.transition(
                    operation_record.operation_id,
                    "recovery_required",
                    status="recovery_required",
                    context={
                        **operation_record.context,
                        "recovery_error_type": type(recovery_error).__name__,
                    },
                )
                operation_store.close()
                operation_resolved = True
                keep_staged = True
                raise RuntimeError(
                    f"Source recovery needs review: marker={marker}, backup={backup}, staged={staged}: {recovery_error}"
                ) from error
            operation_store.complete(operation_record.operation_id, outcome="rolled_back")
            operation_resolved = True
            raise
        operation_record = operation_store.transition(operation_record.operation_id, "source_activated")
        os.chmod(backup, 0o700)
        operation_store.complete(operation_record.operation_id, outcome="succeeded")
        operation_resolved = True
        print(f"Previous setup source retained at {backup}", flush=True)
        return info
    finally:
        if operation_store is not None and operation_record is None:
            operation_store.close()
        elif operation_store is not None and not operation_resolved:
            # Process loss or failed finalization must leave every recorded
            # copy path intact for manual reconciliation.
            operation_store.close()
            keep_staged = True
        if not keep_staged:
            shutil.rmtree(temporary)


def _apply_refresh(
    repository: str, channel: str, info: dict[str, object],
    config: SetupConfig, arguments: list[str], *, expected_revision: str,
) -> int:
    """Hold the setup lock across source activation and target reconciliation."""
    with maintenance_lock() as acquired:
        if not acquired:
            raise RuntimeError("Setup or maintenance is already running; refresh has made no changes")
        _assert_no_source_activation(repository)
        setup_marker = _setup_operation_marker()
        _protected_operation_record(setup_marker)
        # Target setup already owns this marker's kernel lock across its
        # mutations. Hold that same ownership while changing source, including
        # slow staging, so setup cannot start after our marker check.
        with OperationStateStore(setup_marker).ownership():
            if saved_setup_revision() != expected_revision:
                raise ValueError("Saved setup changed before refresh; rerun refresh to use the current configuration")
            _assert_setup_operation_ready(config)
            info = (
                _upgrade_snapshot(repository, channel)
                if info.get("installation_type") == "setup-snapshot" else upgrade_channel(repository)
            )
            # Reconcile only owned source, preserving retained private data.
            _normalize_downloaded_source(repository, runtime_only=True)
            write_refresh_source_metadata(repository)
        print(f"Basaltwater is at {str(info['commit'])[:12]} on {info['channel']}", flush=True)
        print(f"Repeating saved local {config.system_type} setup for {config.username}", flush=True)
        # Release marker ownership before the fresh runner claims setup. A
        # competing setup can win that claim; the runner then refuses overlap.
        # Keep saved options private and start the upgraded runner in a fresh process.
        with tempfile.TemporaryDirectory(prefix="basaltwater-refresh-") as temporary:
            argument_file = Path(temporary) / "setup-args.json"
            write_json_atomic(str(argument_file), [*arguments, "--expected-setup-revision", expected_revision], mode=0o600)
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
    parent = path.parent.stat()
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
    if config.privilege_broker_port is not None and not config.disable_privilege_broker:
        # The remote transport host is intentionally absent from saved setup.
        # Restore the reviewed origin from protected policy instead of changing
        # the live approval endpoint to localhost during a target-side replay.
        from lib.privilege_policy import load_policy

        policy = load_policy()
        origin = urlsplit(policy["origin"])
        if (not origin.hostname or origin.port != config.privilege_broker_port
                or pwd.getpwnam(config.username).pw_uid != policy["requester_uid"]):
            raise ValueError("Saved setup and privilege policy have different port or requester identity")
        # to_remote_args carries the approval host from its transport-host field.
        config.host = origin.hostname
        config.privilege_broker_host = origin.hostname
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
        expected_revision = saved_setup_revision()
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
        from lib.refresh_job import WORKER_ENV, queue_refresh

        # The worker owns its cgroup and output. A T3 restart or an SSH client
        # disappearing must not kill source activation or the setup runner.
        # Panel administration already supplies an independently owned job.
        if (can_manage_system_services(config.machine_type)
                and os.environ.get(WORKER_ENV) != "1"
                and not valid_job_id(os.environ.get(JOB_ID_ENV))):
            return queue_refresh(repository)
        return _apply_refresh(repository, channel, info, config, arguments, expected_revision=expected_revision)
    except (OSError, RuntimeError, ValueError, TypeError, KeyError) as exc:
        print(f"Error: {exc}")
        return 1
