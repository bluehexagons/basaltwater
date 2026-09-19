"""Shared utilities for scheduled tasks (sync, scrub, etc)."""

from __future__ import annotations
import os
import shlex
from typing import Optional, Any, TYPE_CHECKING
from lib.remote_utils import run
from lib.mount_utils import is_path_under_mnt, get_mount_ancestor
from lib.validation import validate_filesystem_path

if TYPE_CHECKING:
    from lib.config import SetupConfig
    from lib.runtime_config import RuntimeConfig





VALID_FREQUENCIES = ['hourly', 'daily', 'weekly', 'biweekly', 'monthly', 'bimonthly']


def validate_sync_paths(source: str, destination: str, *, resolve: bool = True) -> None:
    """Reject mirrors whose deletion or recursion could affect their source."""
    for path in (source, destination):
        validate_filesystem_path(path)
        if not os.path.isabs(path):
            raise ValueError(f"Sync path must be absolute: {path}")
    normalize = os.path.realpath if resolve else os.path.abspath
    source_root, destination_root = normalize(source), normalize(destination)
    if os.path.commonpath([source_root, destination_root]) in {source_root, destination_root}:
        raise ValueError("Sync source and destination must not be equal or nested")


def validate_configured_storage_mounts(path: str, config: Any) -> None:
    """Require the exact declared mount, not merely any mounted ancestor."""
    path = os.path.abspath(path)
    roots = [spec[0] for spec in getattr(config, "smb_mounts", None) or [] if spec]
    roots += [spec[1] for spec in getattr(config, "storage_mounts", None) or [] if len(spec) >= 2]
    for root in roots:
        root = os.path.abspath(root)
        if path == root or path.startswith(root.rstrip(os.sep) + os.sep):
            if os.path.realpath(root) != root or not os.path.ismount(root):
                raise RuntimeError(f"Required configured mount is unavailable: {root}")


def validate_frequency(frequency: str, label: str = "frequency") -> None:
    """Validate frequency parameter.
    
    Args:
        frequency: Frequency to validate
        label: Label for error messages
        
    Raises:
        ValueError: If frequency is invalid
    """
    if frequency not in VALID_FREQUENCIES:
        raise ValueError(
            f"Invalid {label} '{frequency}'. "
            f"Must be one of: {', '.join(VALID_FREQUENCIES)}"
        )


def get_timer_calendar(frequency: str, hour_offset: Optional[int] = None) -> str:
    """Generate systemd timer OnCalendar string for the given frequency.

    Note: For biweekly and bimonthly, the timer runs more frequently than
    the name suggests (weekly and monthly respectively). The actual interval
    enforcement is done by the orchestrator (storage_ops.py) which tracks
    last-run timestamps and skips operations that aren't due yet. This
    approach is necessary because systemd calendar expressions don't support
    14-day or 60-day intervals directly.

    Args:
        frequency: 'hourly', 'daily', 'weekly', 'biweekly', 'monthly', or 'bimonthly'
        hour_offset: Hour to run (0-23), default None uses 2 AM for non-hourly

    Returns:
        OnCalendar string for systemd timer
    """
    if frequency == 'hourly':
        return '*-*-* *:00:00'

    hour = hour_offset if hour_offset is not None else 2

    calendars = {
        'daily': f'*-*-* {hour:02d}:00:00',
        'weekly': f'Mon *-*-* {hour:02d}:00:00',
        'biweekly': f'Mon *-*-* {hour:02d}:00:00',  # Run weekly, orchestrator handles 14-day interval
        'monthly': f'*-*-01 {hour:02d}:00:00',
        'bimonthly': f'*-*-01 {hour:02d}:00:00'  # Run monthly, orchestrator handles 60-day interval
    }
    return calendars.get(frequency, f'*-*-* {hour:02d}:00:00')


def escape_systemd_description(value: str) -> str:
    """Escape value for safe use in systemd Description field."""
    return value.replace("\\", "\\\\").replace("\n", " ").replace('"', "'")


def check_path_on_smb_mount(path: str, config: SetupConfig) -> bool:
    """Check if path is on an SMB mount."""
    return _check_path_on_smb_mount_list(path, config.smb_mounts or [])


def _check_path_on_smb_mount_list(path: str, smb_mounts: list[list[str]] | None) -> bool:
    """Check if path matches any SMB mount in the list.

    This is the shared implementation used by both SetupConfig-based
    and dict-based config loaders.
    """
    if not smb_mounts:
        return False
    for mount_spec in smb_mounts:
        mountpoint = mount_spec[0]
        if path.startswith(mountpoint + '/') or path == mountpoint:
            return True
    return False


def ensure_directory(path: str, username: str) -> None:
    """Ensure a directory exists, warn if under /mnt with no mount point.

    If the directory already exists, no ownership change is performed.
    Ownership is only set when the directory is first created.

    Args:
        path: Directory path to ensure exists
        username: Owner username for the directory
    """
    if os.path.exists(path):
        if not os.path.isdir(path):
            raise NotADirectoryError(f"Path exists but is not a directory: {path}")
        return
    if is_path_under_mnt(path):
        mount_ancestor = get_mount_ancestor(path)
        if not mount_ancestor:
            print(f"  ⚠ Warning: {path} is under /mnt but no mount point found")
            return
    os.makedirs(path, exist_ok=True)
    run(f"chown {shlex.quote(username)}:{shlex.quote(username)} {shlex.quote(path)}")


def get_all_storage_paths(config: "SetupConfig | RuntimeConfig") -> list[str]:
    """Get all unique paths from sync and scrub specs.

    Args:
        config: Setup configuration containing sync_specs and scrub_specs

    Returns:
        List of unique paths from all storage specs
    """
    paths: set[str] = set()

    # Collect paths from sync specs (source and destination)
    for spec in getattr(config, "sync_specs", None) or []:
        if len(spec) >= 2:
            paths.add(spec[0])  # source
            paths.add(spec[1])  # destination

    for spec in getattr(config, "backup_specs", []) or []:
        if len(spec) >= 2:
            paths.add(spec[0])
            paths.add(spec[1])

    # Collect paths from scrub specs (directory and database)
    for spec in getattr(config, "scrub_specs", None) or []:
        if len(spec) >= 2:
            paths.add(spec[0])  # directory
            paths.add(spec[1])  # database

    return sorted(list(paths))


def needs_mount_check(path: str, config: "SetupConfig | RuntimeConfig | dict") -> bool:
    """Check if a path needs mount validation.

    Args:
        path: Path to check
        config: Setup configuration (SetupConfig object, dict, or RuntimeConfig)

    Returns:
        True if path is under /mnt or on an SMB mount
    """
    if is_path_under_mnt(path):
        return True

    # Handle different config types
    # Use getattr to safely access smb_mounts regardless of type
    smb_mounts = getattr(config, 'smb_mounts', None)
    if smb_mounts is None and isinstance(config, dict):
        smb_mounts = config.get('smb_mounts')

    if _check_path_on_smb_mount_list(path, smb_mounts):
        return True
    return False


def has_mount_paths(config: "SetupConfig | RuntimeConfig") -> bool:
    """Check if any storage spec path needs mount validation.

    Args:
        config: Setup configuration

    Returns:
        True if any path is under /mnt or on an SMB mount
    """
    for path in get_all_storage_paths(config):
        if needs_mount_check(path, config):
            return True
    return bool(getattr(config, "storage_mounts", None))


def get_mount_points_from_config(config: SetupConfig) -> set[str]:
    """Get all unique mount points from storage specs.

    Collects mount points for paths that are under /mnt and SMB mount points
    defined in ``config.smb_mounts``.

    Args:
        config: Setup configuration

    Returns:
        Set of mount point paths (including both /mnt-based and SMB mounts)
    """
    mount_points: set[str] = set()

    for path in get_all_storage_paths(config):
        if is_path_under_mnt(path):
            mount_ancestor = get_mount_ancestor(path)
            if mount_ancestor:
                mount_points.add(mount_ancestor)
            else:
                # If /mnt itself is configured, require /mnt to be mounted.
                if path == "/mnt":
                    mount_points.add("/mnt")
                # Derive expected mount root for unmounted paths under /mnt:
                # e.g. /mnt/data/source -> /mnt/data
                parts = path.split('/')
                # '/mnt/data/source'.split('/') -> ['', 'mnt', 'data', 'source']
                # so index 2 is the expected top-level mount directory name.
                if len(parts) >= 3 and parts[2]:
                    mount_points.add(f"/mnt/{parts[2]}")

    smb_mounts = getattr(config, 'smb_mounts', None) or []
    for mount_spec in smb_mounts:
        if mount_spec:
            mount_points.add(mount_spec[0])

    for mount_spec in getattr(config, "storage_mounts", None) or []:
        if len(mount_spec) >= 2:
            mount_points.add(mount_spec[1])

    return mount_points
