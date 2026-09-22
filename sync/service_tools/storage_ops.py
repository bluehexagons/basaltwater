#!/usr/bin/env python3
"""Unified storage operations orchestrator for sync and scrub.

This script is the single entry point for all storage operations.
It reads sync/scrub specs from machine state and executes them in order:
1. Sync operations (rsync source -> destination)
2. Scrub full verify+repair (if interval elapsed)
3. Parity updates (fast mode for new/changed files)

Uses file locking to prevent concurrent runs.
"""

from __future__ import annotations

import sys
import os
import io
import json
import fcntl
import math
import time
from datetime import datetime
from typing import Optional
from logging import ERROR, WARNING
from pathlib import Path

# Add lib directory to path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '../..'))

from lib.logging_utils import get_service_logger, log_event
from lib.atomic_io import write_json_atomic
from lib.notifications import (
    notification_level_from_state,
    parse_notification_args,
    send_notification_safe,
)
from lib.machine_state import load_setup_config
from lib.mount_utils import get_mount_ancestor
from lib.task_utils import needs_mount_check, validate_configured_storage_mounts
from lib.runtime_config import RuntimeConfig
from lib.validation import validate_filesystem_path

# Constants
LOCK_FILE = "/run/lock/storage-ops.lock"
STATE_FILE = "/var/lib/storage-ops/last_run.json"
LOG_DIR = "/var/log/storage-ops"

# Frequency in seconds
FREQUENCY_SECONDS = {
    "hourly": 3600,
    "daily": 86400,
    "weekly": 604800,
    "biweekly": 1209600,  # 14 days
    "monthly": 2592000,
    "bimonthly": 5184000,  # ~60 days
}


class OperationLock:
    """Context manager for storage operations lock."""
    
    def __init__(self, lock_path: str):
        self.lock_path = lock_path
        self.lock_file: Optional[io.TextIOWrapper] = None
        self.acquired = False
    
    def acquire(self, blocking: bool = False, timeout: float = 300.0) -> bool:
        """Acquire lock with optional blocking and timeout."""
        validate_filesystem_path(self.lock_path, must_exist=False)
        # Ensure parent directory exists
        os.makedirs(os.path.dirname(self.lock_path), exist_ok=True)
        
        # Open without truncation so a failed lock attempt doesn't modify the file.
        self.lock_file = open(self.lock_path, 'a+')
        try:
            if blocking:
                start_time = time.time()
                while True:
                    try:
                        fcntl.flock(self.lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        self.acquired = True
                        return True
                    except (IOError, OSError):
                        if time.time() - start_time >= timeout:
                            if self.lock_file:
                                self.lock_file.close()
                                self.lock_file = None
                            return False
                        time.sleep(1.0)
            else:
                try:
                    fcntl.flock(self.lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    self.acquired = True
                    return True
                except (IOError, OSError):
                    if self.lock_file:
                        self.lock_file.close()
                        self.lock_file = None
                    return False
        except Exception:
            if self.lock_file:
                self.lock_file.close()
                self.lock_file = None
            raise
    
    def release(self) -> None:
        """Release the lock while retaining its stable filesystem inode."""
        if self.acquired and self.lock_file:
            try:
                fcntl.flock(self.lock_file.fileno(), fcntl.LOCK_UN)
            except (IOError, OSError):
                pass
            finally:
                try:
                    self.lock_file.close()
                except OSError:
                    pass
                self.acquired = False
                self.lock_file = None
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()
        return False


def load_last_run() -> dict:
    """Load last run timestamps from state file."""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r') as f:
                state = json.load(f)
                return state if isinstance(state, dict) else {}
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def save_last_run(state: dict) -> None:
    """Save last run timestamps atomically."""
    validate_filesystem_path(STATE_FILE, must_exist=False)
    write_json_atomic(STATE_FILE, state, mode=0o600, sort_keys=True)


def is_operation_due(last_run: dict, op_id: str, interval: str, first_run_default: bool = True) -> bool:
    """Check if an operation is due based on its interval.
    
    Args:
        last_run: Dict of operation IDs to last run timestamps
        op_id: Unique operation identifier
        interval: Interval string (e.g. "hourly", "daily", "monthly")
        first_run_default: Whether to return True on first run (when last_time is None).
                          Set to False for expensive operations that shouldn't run immediately.
    
    Returns:
        True if operation should run now
    """
    last_time = last_run.get(op_id)
    if last_time is None:
        return first_run_default
    
    interval_sec = FREQUENCY_SECONDS.get(interval, FREQUENCY_SECONDS["hourly"])
    try:
        timestamp = float(last_time)
    except (TypeError, ValueError, OverflowError):
        return True
    if not math.isfinite(timestamp):
        return True
    elapsed = time.time() - timestamp
    return elapsed < 0 or elapsed >= interval_sec


def resolve_scrub_database_path(directory: str, database: str) -> str:
    """Resolve scrub database path, handling relative paths.
    
    Scrub specs may store the database as a relative path (e.g. '.pardatabase').
    This must be resolved relative to the scrub directory before use.
    """
    if not database.startswith('/'):
        database = os.path.join(directory, database)
    return os.path.normpath(database)


def get_sync_op_id(source: str, destination: str) -> str:
    """Generate unique ID for sync operation."""
    return f"sync:{source}:{destination}"


def get_scrub_op_id(directory: str, database: str) -> str:
    """Generate unique ID for scrub operation."""
    return f"scrub:{directory}:{database}"


def get_parity_op_id(directory: str, database: str) -> str:
    """Generate unique ID for parity update operation."""
    return f"parity:{directory}:{database}"


def validate_mounts_for_operation(paths: list[str], config: RuntimeConfig, operation_type: str) -> tuple[bool, str]:
    """Validate that all required mounts are available.
    
    For paths that don't exist yet (e.g., sync destinations), validates that
    the mount ancestor exists. This allows rsync to create the destination
    directory on a mounted filesystem.
    """
    for path in paths:
        try:
            validate_configured_storage_mounts(path, config)
        except (OSError, ValueError, RuntimeError) as exc:
            return False, str(exc)
        if needs_mount_check(path, config):
            # Check if path exists
            path_exists = os.path.exists(path)
            
            if path_exists:
                # Path exists - validate it's on a mounted filesystem
                mount_ancestor = get_mount_ancestor(path)
                if not mount_ancestor:
                    return False, f"No mounted filesystem found for {path} ({operation_type})"
                if not os.path.ismount(mount_ancestor):
                    return False, f"Mount {mount_ancestor} not available for {operation_type}"
            else:
                # Path doesn't exist - check if parent mount exists
                # Walk up the directory tree to find an existing ancestor
                current = path
                while current and current != '/':
                    parent = os.path.dirname(current)
                    if parent == current:  # Reached root
                        break
                    if os.path.exists(parent):
                        # Found an existing parent - verify it's mounted
                        mount_ancestor = get_mount_ancestor(parent)
                        if not mount_ancestor:
                            return False, f"No mounted filesystem found for {path} (parent {parent} not mounted)"
                        if not os.path.ismount(mount_ancestor):
                            return False, f"Mount {mount_ancestor} not available for {operation_type}"
                        # Mount is valid - path can be created on this filesystem
                        break
                    current = parent
                else:
                    # No existing parent found - error
                    return False, f"Path {path} not available (possibly unmounted)"

    return True, ""


def run_sync(source: str, destination: str, logger) -> tuple[bool, str]:
    """Execute rsync sync operation."""
    from sync.service_tools.sync_rsync import run_rsync_with_notifications
    
    # Logging is handled by run_rsync_with_notifications
    
    try:
        result = run_rsync_with_notifications(source, destination, suppress_notifications=True)
        return result == 0, f"Sync completed with exit code {result}"
    except Exception as e:
        log_event(logger, "Sync failed", level=ERROR, source=source, destination=destination, error=str(e))
        return False, str(e)


def validate_sync_integrity(source: str, destination: str, config: RuntimeConfig) -> tuple[bool, str]:
    """Keep mirrors from overwriting recovery evidence or propagating known damage."""
    from sync.service_tools.scrub_findings import Findings
    from lib.validation import validate_scrub_specs

    def overlaps(first: Path, second: Path) -> bool:
        return first.is_relative_to(second) or second.is_relative_to(first)

    try:
        validate_scrub_specs(config.scrub_specs)
        source_path, destination_path = Path(source).resolve(), Path(destination).resolve()
        for directory, database, _redundancy, _interval in config.scrub_specs:
            database = resolve_scrub_database_path(directory, database)
            root, parity = Path(directory).resolve(), Path(database).resolve()
            if overlaps(destination_path, parity):
                return False, f"Sync destination overlaps scrub database {database}; move parity outside the mirror destination"
            if not any(overlaps(endpoint, root) for endpoint in (source_path, destination_path)):
                continue
            valid, error = validate_mounts_for_operation([directory, database], config, "sync integrity check")
            if not valid:
                return False, error
            report = Findings(directory, database)
            for relative in report.data["files"]:
                if report.active(relative) and any((root / relative).is_relative_to(endpoint)
                                                   for endpoint in (source_path, destination_path)):
                    return False, f"Sync paused for unresolved integrity finding: {root / relative}; inspect with basaltw scrub"
        return True, ""
    except (OSError, ValueError, RuntimeError) as exc:
        return False, f"Cannot establish sync integrity safety: {exc}"


def run_scrub(directory: str, database: str, redundancy: str, verify: bool, logger) -> tuple[bool, str, bool]:
    """Return success, message, and completion independently of integrity findings."""
    from sync.service_tools.scrub_par2 import scrub_directory
    
    mode = "full verify+repair" if verify else "parity update only"
    log_event(logger, "Starting scrub", directory=directory, mode=mode)
    
    log_dir = LOG_DIR
    os.makedirs(log_dir, exist_ok=True)
    
    # Generate log file path for this scrub
    from hashlib import md5
    scrub_id = md5(f"{directory}:{database}".encode()).hexdigest()[:8]
    log_file = f"{log_dir}/scrub-{scrub_id}.log"
    
    try:
        redundancy_int = int(redundancy.rstrip('%'))
        scrub_result = scrub_directory(directory, database, redundancy_int, log_file, verify, suppress_notifications=True)
        unrepairable = scrub_result.get("files_unrepairable", []) if isinstance(scrub_result, dict) else []
        ok = isinstance(scrub_result, dict) and scrub_result.get("ok") is True
        completed = isinstance(scrub_result, dict) and scrub_result.get("completed") is True
        if unrepairable:
            sample = ", ".join(unrepairable[:5])
            extra = f" (and {len(unrepairable) - 5} more)" if len(unrepairable) > 5 else ""
            return False, f"Scrub found {len(unrepairable)} unresolved integrity finding(s) in {directory}: {sample}{extra}. Inspect with basaltw scrub inspect HOST --file PATH", completed
        if not ok:
            return False, f"Scrub did not complete cleanly for {directory}", completed
        return True, f"Scrub completed for {directory}", completed
    except ValueError as e:
        error_msg = f"Invalid redundancy value '{redundancy}': {e}"
        log_event(logger, "Invalid scrub redundancy", level=ERROR, directory=directory, redundancy=redundancy, error=str(e))
        return False, error_msg, False
    except Exception as e:
        log_event(logger, "Scrub failed", level=ERROR, directory=directory, error=str(e))
        return False, str(e), False


def execute_storage_operations() -> dict:
    """Main orchestrator function."""
    # Use console output only (not syslog) since systemd captures stdout/stderr to journal
    # Using both would create duplicate log entries
    logger = get_service_logger('storage-ops', 'operations', use_syslog=False, console_output=True)
    
    results = {
        "syncs": [],
        "scrubs": [],
        "parity_updates": [],
        "start_time": datetime.now().isoformat(),
        "end_time": None,
        "success": True,
    }
    
    # Load configuration
    config_dict = load_setup_config()
    if not config_dict:
        log_event(logger, "No configuration found in machine state", level=ERROR)
        results["success"] = False
        return results
    
    config = RuntimeConfig.from_dict(config_dict)
    sync_specs = config.all_sync_specs()
    notification_configs = parse_notification_args(
        config.notify_specs,
        notification_level=notification_level_from_state(
            config.notification_level,
            logger,
        ),
        strict_https=config.notification_strict_https,
    )
    
    log_event(
        logger,
        "Loaded storage operation specs",
        sync_specs=len(sync_specs),
        backup_specs=len(config.backup_specs),
        scrub_specs=len(config.scrub_specs),
    )
    
    if not config.has_storage_ops():
        log_event(logger, "No storage operations configured")
        results["end_time"] = datetime.now().isoformat()
        return results
    
    # Load last run state
    last_run = load_last_run()
    new_state = last_run.copy()
    baseline_time = time.time()
    full_scrubs_attempted: set[tuple[str, str]] = set()
    for spec in config.scrub_specs:
        if len(spec) == 4:
            scrub_op_id = get_scrub_op_id(spec[0], spec[1])
            if scrub_op_id not in last_run:
                new_state[scrub_op_id] = baseline_time
    
    # Calculate total operations for progress tracking
    total_syncs = sum(1 for spec in sync_specs if len(spec) == 3 and is_operation_due(last_run, get_sync_op_id(spec[0], spec[1]), spec[2]))
    total_scrubs = sum(1 for spec in config.scrub_specs if len(spec) == 4 and is_operation_due(last_run, get_scrub_op_id(spec[0], spec[1]), spec[3], first_run_default=False))
    total_parity = sum(
        1
        for spec in config.scrub_specs
        if len(spec) == 4 and is_operation_due(last_run, get_parity_op_id(spec[0], spec[1]), "daily")
    )
    
    # Log operation summary
    if total_syncs > 0 or total_scrubs > 0 or total_parity > 0:
        log_event(
            logger,
            "Operations due",
            syncs_due=total_syncs,
            scrubs_due=total_scrubs,
            parity_updates_due=total_parity,
        )
    else:
        log_event(logger, "No operations due at this time")
    
    # Execute syncs first (always run if due).  Starts are logged locally;
    # external targets receive the completion/failure summary only.
    for spec in sync_specs:
        if len(spec) != 3:
            log_event(logger, "Invalid sync spec", level=ERROR, spec=spec)
            results["syncs"].append({"spec": spec, "success": False, "error": "Invalid spec"})
            results["success"] = False
            continue
        
        source, destination, interval = spec
        op_id = get_sync_op_id(source, destination)
        
        if not is_operation_due(last_run, op_id, interval):
            # Not due yet - log for visibility
            last_time = last_run.get(op_id)
            if last_time:
                elapsed = time.time() - last_time
                logger.debug(f"Skipping sync {source} -> {destination} ({interval}): ran {elapsed/3600:.1f}h ago")
            else:
                logger.debug(f"Skipping sync {source} -> {destination} ({interval}): never run")
            continue
        
        # Validate mounts
        valid, error_msg = validate_mounts_for_operation([source, destination], config, "sync")
        if valid:
            valid, error_msg = validate_sync_integrity(source, destination, config)
        if not valid:
            log_event(logger, "Skipping sync", level=WARNING, source=source, destination=destination, error=error_msg)
            results["syncs"].append({
                "source": source,
                "destination": destination,
                "success": False,
                "error": error_msg,
                "skipped": True
            })
            results["success"] = False
            continue
        
        success, message = run_sync(source, destination, logger)
        results["syncs"].append({
            "source": source,
            "destination": destination,
            "success": success,
            "message": message
        })
        
        if success:
            new_state[op_id] = time.time()
            save_last_run(new_state)
        else:
            results["success"] = False
    
    # Execute full scrubs if due.  Long-running progress remains in the local
    # journal; the final summary contains the actionable result.
    for spec in config.scrub_specs:
        if len(spec) != 4:
            log_event(logger, "Invalid scrub spec", level=ERROR, spec=spec)
            results["scrubs"].append({"spec": spec, "success": False, "error": "Invalid spec"})
            results["success"] = False
            continue
        
        directory, database, redundancy, interval = spec
        op_id = get_scrub_op_id(directory, database)
        resolved_database = resolve_scrub_database_path(directory, database)
        
        if not is_operation_due(last_run, op_id, interval, first_run_default=False):
            # Not due yet - log for visibility
            last_time = last_run.get(op_id)
            if last_time:
                elapsed = time.time() - last_time
                logger.debug(f"Skipping scrub {directory} ({interval}): ran {elapsed/86400:.1f}d ago")
            else:
                logger.debug(f"Skipping scrub {directory} ({interval}): never run (deferred until scheduled)")
            continue
        
        # Validate mounts
        valid, error_msg = validate_mounts_for_operation([directory, resolved_database], config, "scrub")
        if not valid:
            log_event(logger, "Skipping scrub", level=WARNING, directory=directory, error=error_msg)
            results["scrubs"].append({
                "directory": directory,
                "success": False,
                "error": error_msg,
                "skipped": True
            })
            results["success"] = False
            continue
        
        full_scrubs_attempted.add((directory, resolved_database))
        success, message, completed = run_scrub(directory, resolved_database, redundancy, verify=True, logger=logger)
        results["scrubs"].append({
            "directory": directory,
            "database": resolved_database,
            "success": success,
            "message": message,
            "full": True
        })
        
        # A completed scan with damage must observe the configured interval,
        # while still reporting failure. Only incomplete scans need a retry.
        if completed:
            new_state[op_id] = time.time()
            new_state[get_parity_op_id(directory, database)] = new_state[op_id]
            save_last_run(new_state)
        if not success:
            results["success"] = False
    
    # Execute parity updates daily for scrub specs (no start notification - fast operation)
    for spec in config.scrub_specs:
        if len(spec) != 4:
            continue
        
        directory, database, redundancy, interval = spec
        parity_op_id = get_parity_op_id(directory, database)
        resolved_database = resolve_scrub_database_path(directory, database)

        if (directory, resolved_database) in full_scrubs_attempted:
            # A full scrub already updates parity; do not retry a failed one
            # in a mode that skips verification.
            continue
        
        if not is_operation_due(last_run, parity_op_id, "daily"):
            # Not due yet - log for visibility
            last_time = last_run.get(parity_op_id)
            if last_time:
                elapsed = time.time() - last_time
                logger.debug(f"Skipping parity update {directory} (daily): ran {elapsed/3600:.1f}h ago")
            else:
                logger.debug(f"Skipping parity update {directory} (daily): never run (deferred until scheduled)")
            continue
        
        # Validate mounts
        valid, error_msg = validate_mounts_for_operation([directory, resolved_database], config, "parity update")
        if not valid:
            log_event(logger, "Skipping parity update", level=WARNING, directory=directory, error=error_msg)
            results["parity_updates"].append({
                "directory": directory,
                "success": False,
                "error": error_msg,
                "skipped": True
            })
            results["success"] = False
            continue
        
        success, message, completed = run_scrub(directory, resolved_database, redundancy, verify=False, logger=logger)
        results["parity_updates"].append({
            "directory": directory,
            "database": resolved_database,
            "success": success,
            "message": message
        })
        
        if not success:
            results["success"] = False
        if completed:
            new_state[parity_op_id] = time.time()
            save_last_run(new_state)
    
    # Save updated state
    save_last_run(new_state)
    
    results["end_time"] = datetime.now().isoformat()
    
    # Send notification if configured and operations actually ran
    total_operations = len(results["syncs"]) + len(results["scrubs"]) + len(results["parity_updates"])
    if notification_configs and total_operations > 0:
        send_operation_notification(results, notification_configs, logger, 
                                   friendly_name=config.friendly_name)
    elif total_operations == 0:
        log_event(logger, "No operations executed - all tasks skipped", reason="not_due_or_validation_failed")
    
    return results


def send_operation_notification(results: dict, notification_configs: list, logger, 
                                friendly_name: str | None = None) -> None:
    """Send summary notification for operations."""
    success_count = sum(1 for s in results["syncs"] if s.get("success"))
    total_syncs = len(results["syncs"])
    
    scrub_success_count = sum(1 for s in results["scrubs"] if s.get("success"))
    total_scrubs = len(results["scrubs"])
    
    parity_success_count = sum(1 for s in results["parity_updates"] if s.get("success"))
    total_parity = len(results["parity_updates"])
    
    name_prefix = f"[{friendly_name}] " if friendly_name else ""
    
    if results["success"]:
        status = "good"
        subject = f"{name_prefix}Storage operations completed"
    else:
        status = "error"
        subject = f"{name_prefix}Storage operations completed with errors"
    
    message = f"Syncs: {success_count}/{total_syncs}, Scrubs: {scrub_success_count}/{total_scrubs}, Parity updates: {parity_success_count}/{total_parity}"
    
    details = f"""Storage Operations Summary
==========================
Start: {results["start_time"]}
End: {results["end_time"]}

Sync Operations:
{format_operation_results(results["syncs"])}

Full Scrub Operations:
{format_operation_results(results["scrubs"])}

Parity Update Operations:
{format_operation_results(results["parity_updates"])}
"""
    
    try:
        send_notification_safe(
            notification_configs,
            subject=subject,
            job="storage-ops",
            status=status,
            message=message,
            details=details,
            logger=logger,
            event_type="storage.operations",
            state="success" if results["success"] else "firing",
            dedup_key="storage-ops:latest-run",
            delivery_policy="signal",
        )
    except Exception as e:
        log_event(logger, "Failed to send operation notification", level=ERROR, error=str(e))


def format_operation_results(operations: list) -> str:
    """Format operation results for notification."""
    if not operations:
        return "  None"
    
    lines = []
    for op in operations:
        success_mark = "✓" if op.get("success") else "✗"
        if op.get("skipped"):
            success_mark = "○"
        
        if "source" in op:
            name = f"{op['source']} -> {op['destination']}"
        elif "directory" in op:
            name = op['directory']
        else:
            name = str(op.get("spec", "unknown"))
        
        lines.append(f"  {success_mark} {name}")
        if op.get("error"):
            lines.append(f"    Error: {op['error']}")
        elif op.get("message"):
            lines.append(f"    {op['message']}")
    
    return "\n".join(lines)


def main():
    """Main entry point."""
    # Use console output only (not syslog) since systemd captures stdout/stderr to journal
    # Using both would create duplicate log entries
    logger = get_service_logger('storage-ops', 'lock', use_syslog=False, console_output=True)
    
    # Load configuration for notifications
    config_dict = load_setup_config()
    notification_configs = []
    friendly_name = None
    if config_dict:
        notification_configs = parse_notification_args(
            config_dict.get('notify_specs', []),
            notification_level=notification_level_from_state(
                config_dict.get('notification_level'),
                logger,
            ),
            strict_https=config_dict.get('notification_strict_https') is True,
        )
        friendly_name = config_dict.get('friendly_name')
    
    # Acquire lock (non-blocking) - if another instance is running, exit cleanly
    with OperationLock(LOCK_FILE) as lock:
        if not lock.acquire(blocking=False):
            log_event(logger, "Another storage-ops instance is already running, skipping this run")
        
            # Send notification about lock failure
            if notification_configs:
                name_prefix = f"[{friendly_name}] " if friendly_name else ""
                try:
                    send_notification_safe(
                        notification_configs,
                        subject=f"{name_prefix}Storage operations skipped",
                        job="storage-ops",
                        status="warning",
                        message="Another storage-ops instance is already running",
                        details="Cannot acquire lock - another instance is in progress",
                        logger=logger
                    )
                except Exception as e:
                    log_event(logger, "Failed to send lock failure notification", level=ERROR, error=str(e))
        
            return 0
    
        # Execute operations (individual start notifications sent for long-running operations)
        results = execute_storage_operations()
        
        # Return exit code based on success
        return 0 if results["success"] else 1


if __name__ == '__main__':
    sys.exit(main())
