#!/usr/bin/env python3
"""Par2 scrub operations for data integrity checking.

This script creates par2 parity files, verifies files, and repairs corrupted files.
Includes structured operation logging and explicit failure reporting.
Supports sending notifications on completion or failure.
"""

from __future__ import annotations

import sys
import os
import re
import stat
import subprocess
import time
from glob import glob, escape
from pathlib import Path
from datetime import datetime
from typing import Optional, Any

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '../..'))

from lib.logging_utils import get_rotating_logger, log_message
from lib.operation_log import create_operation_logger
from lib.validation import validate_filesystem_path
from lib.disk_utils import estimate_operation_duration
from lib.progress_utils import ProgressTracker, ProgressMessage

PAR2_EXTENSION = ".par2"
PAR2_MTIME_TOLERANCE_SECONDS = 1.0
PAR2_CREATE_RETRIES = 3
PAR2_CREATE_BACKOFF_SECONDS = 2
PAR2_CREATE_MAX_BACKOFF_SECONDS = 30

_LOGGERS: dict[str, Any] = {}


def _confined_path(path: str, root: str) -> None:
    """Reject traversal and symlinks, including existing parent components."""
    validate_filesystem_path(path)
    absolute = Path(os.path.abspath(path))
    base = Path(os.path.abspath(root))
    if not absolute.is_relative_to(base):
        raise ValueError(f"Path escapes scrub root: {path}")
    for component in (absolute, *absolute.parents):
        if component.is_symlink():
            raise ValueError(f"Symlinks are not allowed in scrub paths: {component}")
    if not absolute.resolve().is_relative_to(base.resolve()):
        raise ValueError(f"Resolved path escapes scrub root: {path}")


def _scan_tree(directory: str) -> list[tuple[str, list[str], list[str]]]:
    """Finish a confined inventory before allowing parity mutations."""
    _confined_path(directory, directory)
    if not os.path.isdir(directory):
        raise ValueError(f"Scrub root is not an existing directory: {directory}")

    def fail(error: OSError) -> None:
        raise error

    entries = []
    for root, dirs, files in os.walk(directory, onerror=fail):
        for names, expected_type in ((dirs, stat.S_ISDIR), (files, stat.S_ISREG)):
            for name in names:
                path = os.path.join(root, name)
                _confined_path(path, directory)
                if not expected_type(os.stat(path, follow_symlinks=False).st_mode):
                    raise ValueError(f'Scrub inventory contains a non-regular entry: {path}')
        entries.append((root, dirs, files))
    return entries


def _parity_files(par2_base: str) -> list[str]:
    """Return only the exact base and recognized PAR2 volume names."""
    candidates = glob(f"{escape(par2_base)}*")
    if par2_base.endswith(PAR2_EXTENSION):
        candidates += glob(f"{escape(par2_base[:-5])}.vol*.par2")
    return sorted({path for path in candidates
                   if _par2_base_from_parity_file(path) == par2_base})


def log(message: str, log_file: str) -> None:
    """Append message to log file and print to console for systemd journal."""
    logger = _LOGGERS.get(log_file)
    if logger is None:
        logger = get_rotating_logger(f"scrub_par2:{log_file}", log_file)
        _LOGGERS[log_file] = logger
    log_message(logger, message)
    # Also print to stdout for systemd journal capture
    print(message, flush=True)


def _remove_par2_files(par2_base: str, log_file: str) -> None:
    """Remove par2 files for a base path (including volume files).
    
    Par2 can create either:
    - Base file: filename.par2 (with -n2+)
    - Volume files: filename.par2.vol00+01.par2 (when base exists)
    - Volume-only: filename.vol00+01.par2 (with -n1, strips .par2 before adding .vol)
    """
    files_to_remove = _parity_files(par2_base)
    
    if files_to_remove:
        log(f"Removing {len(files_to_remove)} existing par2 file(s)", log_file)
    for par2_file in files_to_remove:
        _confined_path(par2_file, os.path.dirname(par2_base))
        try:
            os.remove(par2_file)
        except (IOError, OSError) as e:
            log(f"Error removing par2 file {par2_file}: {e}", log_file)
            raise


def create_par2(
    file_path: str,
    directory: str,
    database: str,
    redundancy: int,
    log_file: str,
    force: bool = False,
    operation_logger: Optional[Any] = None
) -> bool:
    """Create par2 parity file if it doesn't exist.
    
    Args:
        file_path: Path to file to protect
        directory: Base directory being protected
        database: Database directory for par2 files
        redundancy: Redundancy percentage
        log_file: Log file path
        force: Whether to recreate existing par2 files
        operation_logger: Optional operation logger for enhanced logging
        
    Returns:
        True if created or already exists, False on error
    """
    relative_path = os.path.relpath(file_path, directory)
    par2_base = os.path.join(database, f"{relative_path}{PAR2_EXTENSION}")
    
    # Enhanced validation
    try:
        _confined_path(file_path, directory)
        _confined_path(par2_base, database)
        validate_filesystem_path(file_path, must_exist=True)
        validate_filesystem_path(database, check_writable=True)
    except ValueError as e:
        log(f"Validation error for {relative_path}: {e}", log_file)
        if operation_logger:
            operation_logger.log_error("validation_failed", str(e), {"file": relative_path})
        return False
    
    # Skip 0-byte files silently (par2 cannot create parity for empty files)
    try:
        if os.path.getsize(file_path) == 0:
            return True
    except OSError:
        return False
    
    # Check for existing par2 files (base and/or volume files)
    # Par2 with -n1 creates volume-only files like filename.vol00+01.par2 (strips .par2 first)
    # Par2 with -n2+ creates base file filename.par2 and volumes filename.par2.vol00+01.par2
    
    # Pattern 1: Base file and volumes that append to it
    par2_files = _parity_files(par2_base)
    
    for parity_file in par2_files:
        _confined_path(parity_file, database)
    
    if par2_files:
        if not force:
            # Check if par2 files are newer than source file
            # Check the newest par2 file (could be base or volume file)
            try:
                file_mtime = os.path.getmtime(file_path)
                par2_mtime = max(os.path.getmtime(f) for f in par2_files)
                
                if file_mtime <= par2_mtime + PAR2_MTIME_TOLERANCE_SECONDS:
                    # Silently skip - file is up to date
                    if operation_logger:
                        operation_logger.log_step("par2_check", "completed", f"Par2 up-to-date: {relative_path}")
                    return True
                force = True
            except OSError as e:
                log(f"Cannot check file times for {relative_path}: {e}, forcing recreation", log_file)
                force = True
        
        # Only remove if we're forcing recreation (file was modified or check failed)
        if force:
            # Atomic removal of existing par2 files
            def remove_existing_par2():
                _remove_par2_files(par2_base, log_file)

            remove_existing_par2()
        else:
            # Files exist and are up-to-date, silently skip
            return True
    
    # Only log when actually creating par2 (not for every file check)
    
    # Don't log file size or creation message for every file
    # Progress will be logged periodically during scrub
    
    # Estimate operation duration
    try:
        file_size = os.path.getsize(file_path)
        estimated_duration = estimate_operation_duration('par2', file_size // (1024 * 1024))
        if operation_logger:
            operation_logger.log_metric("estimated_duration_seconds", estimated_duration, "seconds")
    except OSError:
        estimated_duration = 60  # Default 1 minute
    
    os.makedirs(os.path.dirname(par2_base), exist_ok=True)
    
    def create_par2_atomic():
        for attempt in range(PAR2_CREATE_RETRIES):
            try:
                start_time = time.time()
                subprocess.run(
                    ['par2', 'create', '-B', directory, f'-r{redundancy}', '-n1', par2_base, relative_path],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    check=True,
                    text=True,
                    cwd=directory
                )
                
                creation_time = time.time() - start_time
                # Only log individual file creation if it took a long time (>5s) or failed
                if creation_time > 5.0:
                    log(f"✓ Created par2 for {relative_path} in {creation_time:.1f}s", log_file)
                if operation_logger:
                    operation_logger.log_metric("par2_creation_time_seconds", creation_time, "seconds")
                    operation_logger.log_metric("par2_file_size_mb", os.path.getsize(file_path) // (1024 * 1024), "MB")
                
                return True
                
            except subprocess.CalledProcessError as e:
                error_msg = f"Error creating par2 for {relative_path} (attempt {attempt + 1}): {e.stdout}"
                log(error_msg, log_file)
                if operation_logger:
                    operation_logger.log_error("par2_creation_failed", error_msg, 
                                          {"file": relative_path, "attempt": attempt + 1})
                
                _remove_par2_files(par2_base, log_file)
                if attempt < PAR2_CREATE_RETRIES - 1:
                    delay = min(PAR2_CREATE_BACKOFF_SECONDS * (2 ** attempt), PAR2_CREATE_MAX_BACKOFF_SECONDS)
                    log(f"Retrying par2 create for {relative_path} in {delay}s", log_file)
                    time.sleep(delay)
        
        return False
    
    return create_par2_atomic()


def _par2_base_from_parity_file(parity_path: str) -> str:
    """Get par2 base path from any parity file.
    
    Handles two volume file formats:
    - Base + volume: filename.par2.vol00+01.par2
    - Volume-only: filename.vol00+01.par2 (created with -n1, no base file)
    """
    if re.search(r'\.vol\d+\+\d+\.par2$', parity_path):
        base = re.sub(r'\.vol\d+\+\d+\.par2$', '', parity_path)
        return base if base.endswith(PAR2_EXTENSION) else base + PAR2_EXTENSION
    return parity_path


def _cleanup_orphan_par2(
    directory: str,
    database: str,
    existing_files: set[str],
    log_file: str,
    operation_logger: Optional[Any] = None
) -> None:
    """Remove parity files for data files that no longer exist."""
    checked_bases: set[str] = set()
    orphan_count = 0
    total_orphan_size = 0
    
    for root, _, files in _scan_tree(database):
        for filename in files:
            if not filename.endswith(PAR2_EXTENSION):
                continue
            par2_path = os.path.join(root, filename)
            par2_base = _par2_base_from_parity_file(par2_path)
            if par2_base in checked_bases:
                continue
            checked_bases.add(par2_base)
            relative_par2 = os.path.relpath(par2_base, database)
            if relative_par2.endswith(PAR2_EXTENSION):
                relative_data = relative_par2[:-len(PAR2_EXTENSION)]
            else:
                relative_data = relative_par2
            if relative_data in existing_files:
                continue
            
            # Enhanced orphan validation
            try:
                # Calculate orphan size before removal
                orphan_par2_files = _parity_files(par2_base)
                orphan_size = sum(os.path.getsize(f) for f in orphan_par2_files if os.path.exists(f))
                total_orphan_size += orphan_size
                
                log(f"Removing orphan par2 for deleted file: {relative_data} ({orphan_size // 1024}KB)", log_file)
                if operation_logger:
                    operation_logger.log_metric("orphan_file_removed", relative_data, "filename")
                    operation_logger.log_metric("orphan_size_kb", orphan_size // 1024, "KB")
                
                def remove_orphan():
                    _remove_par2_files(par2_base, log_file)
                
                remove_orphan()
                
                orphan_count += 1
                
            except OSError as e:
                log(f"Error removing orphan par2 for {relative_data}: {e}", log_file)
                if operation_logger:
                    operation_logger.log_error("orphan_removal_failed", str(e), 
                                          {"file": relative_data})
                raise
    
    if orphan_count > 0:
        log(f"Cleaned up {orphan_count} orphan par2 sets, freed {total_orphan_size // 1024 // 1024}MB", log_file)
        if operation_logger:
            operation_logger.log_metric("total_orphan_files_removed", orphan_count, "count")
            operation_logger.log_metric("total_orphan_size_mb", total_orphan_size // 1024 // 1024, "MB")


# Verification outcomes returned by verify_repair.
VERIFY_OK = "ok"
VERIFY_REPAIRED = "repaired"
VERIFY_UNREPAIRABLE = "unrepairable"


def verify_repair(file_path: str, directory: str, database: str, log_file: str) -> str:
    """Verify file integrity and repair if needed.

    Args:
        file_path: Path to file to verify
        directory: Base directory being protected
        database: Database directory for par2 files
        log_file: Log file path

    Returns:
        One of VERIFY_OK (verification passed or no parity exists),
        VERIFY_REPAIRED (corruption detected and repaired), or
        VERIFY_UNREPAIRABLE (corruption detected and repair failed).
    """
    relative_path = os.path.relpath(file_path, directory)
    par2_base = os.path.join(database, f"{relative_path}{PAR2_EXTENSION}")

    _confined_path(file_path, directory)
    _confined_path(par2_base, database)
    parity_files = _parity_files(par2_base)
    for parity_file in parity_files:
        _confined_path(parity_file, database)
    if not parity_files:
        return VERIFY_OK
    par2_base = par2_base if os.path.exists(par2_base) else parity_files[0]

    # Don't log every file verification - only failures and repairs

    try:
        subprocess.run(
            ['par2', 'verify', '-B', directory, par2_base],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=True,
            text=True,
            cwd=directory
        )
        return VERIFY_OK
    except subprocess.CalledProcessError:
        log(f"Verification failed for: {relative_path}", log_file)
        log("Attempting repair...", log_file)

        try:
            subprocess.run(
                ['par2', 'repair', '-B', directory, par2_base],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=True,
                text=True,
                cwd=directory
            )
            log(f"✓ Repaired: {relative_path}", log_file)
            return VERIFY_REPAIRED
        except subprocess.CalledProcessError as e:
            log(f"✗ Repair failed: {relative_path}", log_file)
            log(f"  Error: {e.stdout}", log_file)
            return VERIFY_UNREPAIRABLE


def scrub_directory(directory: str, database: str, redundancy: int, log_file: str, verify: bool = True, suppress_notifications: bool = False) -> dict:
    """Scrub directory: create par2 files and optionally verify/repair.
    
    Args:
        directory: Directory to scrub
        database: Database directory for par2 files
        redundancy: Redundancy percentage
        log_file: Log file path
        verify: Whether to verify and repair (False for fast initial creation)
        suppress_notifications: If True, skip sending notifications (caller will handle)

    Returns:
        Result dict with keys: ok (bool), completed (bool), files_processed, files_created,
        files_updated, files_verified, files_repaired, files_failed, and
        files_unrepairable (lists of relative paths). ``ok`` is False when
        validation or parity creation failed or any files could not be repaired.
        ``completed`` is True only after scanning and cleanup without operational
        failures; finding unrepairable files does not make a scan incomplete.
    """
    result: dict = {
        "ok": True,
        "completed": False,
        "files_processed": 0,
        "files_created": 0,
        "files_updated": 0,
        "files_verified": 0,
        "files_repaired": 0,
        "files_failed": [],
        "files_unrepairable": [],
    }
    # Load notification configs from machine state
    notification_configs = []
    friendly_name = None
    if not suppress_notifications:
        try:
            from lib.machine_state import load_setup_config
            from lib.notifications import (
                notification_level_from_state,
                parse_notification_args,
            )
            setup_config = load_setup_config()
            if setup_config:
                if 'notify_specs' in setup_config:
                    notif_logger = _LOGGERS.get(log_file)
                    if notif_logger is None:
                        notif_logger = get_rotating_logger(
                            f"scrub_par2:{log_file}",
                            log_file,
                        )
                        _LOGGERS[log_file] = notif_logger
                    notification_configs = parse_notification_args(
                        setup_config['notify_specs'],
                        notification_level=notification_level_from_state(
                            setup_config.get('notification_level'),
                            notif_logger,
                        ),
                        strict_https=(
                            setup_config.get('notification_strict_https') is True
                        ),
                    )
                friendly_name = setup_config.get('friendly_name')
        except (ImportError, OSError, ValueError, KeyError, TypeError) as e:
            # If notification loading fails, just log and continue without notifications
            log(f"Warning: Failed to load notification configs: {e}", log_file)
    
    # Enhanced logging with operation logger
    operation_logger = create_operation_logger(
        "scrub_par2", 
        directory=directory, 
        database=database, 
        redundancy=redundancy, 
        verify=verify
    )
    try:
        log("=" * 60, log_file)
        log(f"Scrub started: {datetime.now()}", log_file)
        log(f"Directory: {directory}", log_file)
        log(f"Database: {database}", log_file)
        log(f"Redundancy: {redundancy}%", log_file)
        log(f"Verify: {verify}", log_file)
        log("=" * 60, log_file)
        
        operation_logger.log_step("scrub_initiated", "started", 
                               f"Starting scrub of {directory} with {redundancy}% redundancy")
        
        try:
            validate_filesystem_path(directory, must_exist=True)
            validate_filesystem_path(database, check_writable=True)
        except ValueError as e:
            operation_logger.log_error("validation_failed", "Directory validation failed")
            log("Validation failed, aborting scrub", log_file)
            
            # Send validation failure notification
            if notification_configs:
                try:
                    from lib.notifications import send_notification_safe
                    # Reuse existing logger from _LOGGERS cache
                    notif_logger = _LOGGERS.get(log_file)
                    send_notification_safe(
                        notification_configs,
                        subject="Error: Scrub validation failed",
                        job="scrub",
                        status="error",
                        message=f"Validation failed for {directory}",
                        details=None,
                        logger=notif_logger
                    )
                except Exception as notify_err:
                    log(f"Warning: Failed to send notification: {notify_err}", log_file)
            
            operation_logger.complete("failed", f"Scrub validation failed: {e}")
            result["ok"] = False
            return result
        
        source_inventory = _scan_tree(directory)
        _confined_path(database, database)
        if Path(directory).resolve().is_relative_to(Path(database).resolve()):
            raise ValueError('Scrub database must not contain the source directory')
        if os.path.isdir(database):
            _scan_tree(database)
        os.makedirs(database, exist_ok=True)
        
        database_path = Path(database).resolve()
        existing_files: set[str] = set()
        files_processed = 0
        files_updated = 0
        files_verified = 0
        files_repaired = 0
        files_failed: list[str] = []
        files_unrepairable: list[str] = []
        files_created = 0  # Track newly created par2 files
        files_skipped_empty = 0  # Track 0-byte files skipped
        files_skipped_uptodate = 0  # Track files with up-to-date par2
        total_file_size = 0
        start_time = time.time()
        
        # Initialize progress tracker with custom log function
        progress_tracker = ProgressTracker(
            interval_seconds=30,
            log_func=lambda msg: log(msg, log_file)
        )
        
        mode_str = "verify+repair" if verify else "parity update"
        log(f"Starting {mode_str} for {directory}", log_file)
        log(f"Scanning directory tree: {directory}", log_file)
        
        files_found = 0
        dirs_found = 0
        for root, dirs, files in source_inventory:
            dirs_found += len(dirs)
            files_found += len(files)
            
            root_path = Path(root).resolve()
            
            if root_path == database_path or database_path in root_path.parents:
                dirs[:] = []
                continue
            
            dirs[:] = [d for d in dirs 
                       if not _is_under_database(root_path / d, database_path)]
            
            for filename in files:
                file_path = os.path.join(root, filename)
                relative_path = os.path.relpath(file_path, directory)
                existing_files.add(relative_path)
                par2_base = os.path.join(database, f"{relative_path}{PAR2_EXTENSION}")
                parity_files = _parity_files(par2_base)
                force = False
                is_new_par2 = not parity_files
                
                try:
                    file_size = os.path.getsize(file_path)
                    if file_size == 0:
                        files_skipped_empty += 1
                        # Existing parity can diagnose a file truncated to zero.
                        # Never replace it with parity for the empty contents.
                        if verify and parity_files:
                            outcome = verify_repair(file_path, directory, database, log_file)
                            files_verified += 1
                            if outcome == VERIFY_REPAIRED:
                                files_repaired += 1
                            elif outcome == VERIFY_UNREPAIRABLE:
                                files_unrepairable.append(relative_path)
                        continue  # Skip 0-byte files (create_par2 will skip them anyway)
                    total_file_size += file_size
                except OSError:
                    files_failed.append(relative_path)
                    continue
                
                if parity_files:
                    try:
                        parity_mtime = max(os.path.getmtime(path) for path in parity_files)
                        if os.path.getmtime(file_path) > parity_mtime + PAR2_MTIME_TOLERANCE_SECONDS:
                            # Don't log every update - will be in periodic progress
                            force = True
                            files_updated += 1
                    except (IOError, OSError) as e:
                        log(f"Error checking par2 timestamps for {relative_path}: {e}", log_file)
                        force = True
                
                success = create_par2(file_path, directory, database, redundancy, log_file, 
                                    force=force, operation_logger=operation_logger)
                if success:
                    files_processed += 1
                    if is_new_par2:
                        files_created += 1
                    elif not force:
                        # File was skipped because par2 is up-to-date
                        files_skipped_uptodate += 1
                else:
                    files_failed.append(relative_path)
                
                # Log progress periodically with detailed stats
                if progress_tracker.should_log():
                    msg = (ProgressMessage("Progress")
                           .add_custom(f"{files_processed} files processed ({files_created} new, {files_updated} updated)")
                           .add_bytes(total_file_size, label="processed")
                           .add_duration(progress_tracker.get_elapsed_seconds())
                           .add_custom("[scanning...]"))
                    
                    if verify:
                        msg.add_custom(f"Verified: {files_verified}, Repaired: {files_repaired}")
                    
                    progress_tracker.force_log(msg.build())
                
                if verify:
                    outcome = verify_repair(file_path, directory, database, log_file)
                    files_verified += 1
                    if outcome == VERIFY_REPAIRED:
                        files_repaired += 1
                    elif outcome == VERIFY_UNREPAIRABLE:
                        files_unrepairable.append(relative_path)
        
        log(f"Directory scan complete: {files_found} files in {dirs_found} directories", log_file)
        
        # Enhanced orphan cleanup
        log("Starting orphan cleanup...", log_file)
        _cleanup_orphan_par2(directory, database, existing_files, log_file, 
                           operation_logger=operation_logger)
        
        # Final metrics
        operation_logger.log_metric("files_processed", files_processed, "count")
        operation_logger.log_metric("files_created", files_created, "count")
        operation_logger.log_metric("files_updated", files_updated, "count")
        operation_logger.log_metric("files_skipped_empty", files_skipped_empty, "count")
        operation_logger.log_metric("files_skipped_uptodate", files_skipped_uptodate, "count")
        operation_logger.log_metric("files_verified", files_verified, "count")
        operation_logger.log_metric("files_repaired", files_repaired, "count")
        operation_logger.log_metric("files_failed", len(files_failed), "count")
        operation_logger.log_metric("files_unrepairable", len(files_unrepairable), "count")
        operation_logger.log_metric("total_file_size_mb", total_file_size // (1024 * 1024), "MB")
        
        operation_status = "failed" if files_failed or files_unrepairable else "completed"
        operation_logger.log_step(
            "scrub_completed",
            operation_status,
            f"Processed {files_processed} files with {len(files_failed)} creation failures",
        )
        
        completion_label = "completed" if operation_status == "completed" else "finished with errors"
        log(f"Scrub {completion_label}: {datetime.now()}", log_file)
        log(
            f"Files: {files_processed} processed, {files_created} created, "
            f"{files_updated} updated, {len(files_failed)} creation failures, "
            f"{files_verified} verified, {files_repaired} repaired, "
            f"{len(files_unrepairable)} UNREPAIRABLE",
            log_file,
        )
        if files_unrepairable:
            log("Unrepairable files:", log_file)
            for unrepairable in files_unrepairable:
                log(f"  - {unrepairable}", log_file)
        if files_skipped_empty > 0 or files_skipped_uptodate > 0:
            log(f"Skipped: {files_skipped_empty} empty files, {files_skipped_uptodate} up-to-date files", log_file)
        log("", log_file)
        
        # Log completion summary
        summary_symbol = "✓" if operation_status == "completed" else "✗"
        summary_msg = (
            f"{summary_symbol} Scrub {completion_label}: {files_processed} processed, "
            f"{files_created} new, {files_updated} updated, {len(files_failed)} failed"
        )
        if verify:
            summary_msg += f", {files_verified} verified, {files_repaired} repaired"
        log(summary_msg, log_file)
        
        operation_logger.complete(
            operation_status,
            f"Scrub completed: {files_processed} files processed, "
            f"{len(files_failed)} failed, {files_repaired} repaired",
        )
        
        # Send completion notification (escalate to error if any files could not be repaired)
        if notification_configs:
            try:
                from lib.notifications import send_notification_safe
                name_prefix = f"[{friendly_name}] " if friendly_name else ""
                if files_failed or files_unrepairable:
                    status = "error"
                    subject_state = "ERROR"
                elif files_repaired > 0:
                    status = "warning"
                    subject_state = "Warning"
                else:
                    status = "good"
                    subject_state = "Success"
                message = f"Processed {files_processed} files"
                if files_created > 0:
                    message += f", created {files_created} new"
                if files_updated > 0:
                    message += f", updated {files_updated}"
                if files_repaired > 0:
                    message += f", repaired {files_repaired}"
                if files_failed:
                    message += f", parity creation failed: {len(files_failed)}"
                if files_unrepairable:
                    message += f", UNREPAIRABLE: {len(files_unrepairable)}"
                if files_verified > 0:
                    message += f", verified {files_verified}"

                details = f"""Scrub Summary:
Directory: {directory}
Files processed: {files_processed}
Files created: {files_created}
Files updated: {files_updated}
Files verified: {files_verified}
Files repaired: {files_repaired}
Parity creation failures: {len(files_failed)}
Files unrepairable: {len(files_unrepairable)}
Total size: {total_file_size // (1024 * 1024)} MB
Redundancy: {redundancy}%
"""
                if files_unrepairable:
                    # Cap listing to avoid blowing past webhook payload limits.
                    sample = files_unrepairable[:50]
                    details += "\nUnrepairable files:\n" + "\n".join(f"  - {p}" for p in sample)
                    if len(files_unrepairable) > len(sample):
                        details += f"\n  ... and {len(files_unrepairable) - len(sample)} more"

                # Reuse existing logger from _LOGGERS cache
                notif_logger = _LOGGERS.get(log_file)
                send_notification_safe(
                    notification_configs,
                    subject=f"{name_prefix}{subject_state}: Scrub completed",
                    job="scrub",
                    status=status,
                    message=message,
                    details=details,
                    logger=notif_logger,
                    event_type="scrub.completed",
                    state="success" if status == "good" else "firing",
                    dedup_key=f"scrub:{directory}:{database}",
                    delivery_policy="signal",
                )
            except Exception as notify_err:
                log(f"Warning: Failed to send notification: {notify_err}", log_file)

        result["files_processed"] = files_processed
        result["files_created"] = files_created
        result["files_updated"] = files_updated
        result["files_verified"] = files_verified
        result["files_repaired"] = files_repaired
        result["files_failed"] = list(files_failed)
        result["files_unrepairable"] = list(files_unrepairable)
        result["ok"] = not files_failed and not files_unrepairable
        result["completed"] = not files_failed
        return result

    except Exception as e:
        operation_logger.log_error("scrub_failed", str(e))
        operation_logger.complete("failed", f"Scrub failed: {e}")
        log(f"Scrub failed: {e}", log_file)
        
        # Send error notification
        if notification_configs:
            try:
                from lib.notifications import send_notification_safe
                name_prefix = f"[{friendly_name}] " if friendly_name else ""
                # Reuse existing logger from _LOGGERS cache
                notif_logger = _LOGGERS.get(log_file)
                send_notification_safe(
                    notification_configs,
                    subject=f"{name_prefix}Error: Scrub failed",
                    job="scrub",
                    status="error",
                    message=f"Scrub failed for {directory}: {str(e)}",
                    details=None,
                    logger=notif_logger
                )
            except Exception as notify_err:
                log(f"Warning: Failed to send error notification: {notify_err}", log_file)
        
        raise


def _is_under_database(path: Path, database_path: Path) -> bool:
    """Check if path is under database directory."""
    path_resolved = path.resolve()
    return path_resolved == database_path or database_path in path_resolved.parents


def main():
    """Main entry point."""
    if len(sys.argv) < 5:
        print("Usage: scrub_par2.py <directory> <database> <redundancy> <log_file> [--no-verify]")
        return 1
    
    directory = sys.argv[1]
    database = sys.argv[2]
    redundancy = int(sys.argv[3])
    log_file = sys.argv[4]
    verify = '--no-verify' not in sys.argv
    
    try:
        result = scrub_directory(directory, database, redundancy, log_file, verify)
        return 0 if result.get('ok', False) else 1
    except Exception as e:
        log(f"Error: {e}", log_file)
        return 1


if __name__ == '__main__':
    sys.exit(main())
