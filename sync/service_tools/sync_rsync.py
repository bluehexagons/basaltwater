#!/usr/bin/env python3
"""Rsync wrapper with notification support for sync operations."""

from __future__ import annotations

import sys
import os
import subprocess
import re
import select
import time
from logging import ERROR, WARNING
from datetime import datetime
from typing import IO

# Add lib directory to path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '../..'))

from lib.logging_utils import get_service_logger, log_event
from lib.progress_utils import ProgressTracker, ProgressMessage, format_bytes

# Conversion constants
BYTES_TO_MB = 1024 * 1024
BYTES_TO_GB = 1024 * 1024 * 1024

# Progress logging interval (seconds)
PROGRESS_LOG_INTERVAL = 30


def _parse_size(size_str: str) -> int:
    """Parse rsync size string (e.g., '1.23G', '456.78M', '789K') to bytes."""
    size_str = size_str.strip()
    multipliers = {
        'K': 1024,
        'M': 1024 * 1024,
        'G': 1024 * 1024 * 1024,
        'T': 1024 * 1024 * 1024 * 1024,
    }
    
    # Match pattern like "1.23G" or "456.78M"
    match = re.match(r'([\d.]+)([KMGT]?)', size_str)
    if match:
        value, unit = match.groups()
        try:
            num = float(value)
            if unit:
                return int(num * multipliers.get(unit, 1))
            return int(num)
        except ValueError:
            pass
    return 0


def run_rsync_with_notifications(source: str, destination: str, suppress_notifications: bool = False) -> int:
    """Run rsync and send notifications on completion or failure.
    
    Args:
        source: Source directory
        destination: Destination directory
        suppress_notifications: If True, skip sending notifications (caller will handle)
        
    Returns:
        Exit code (0 for success, non-zero for failure)
    """
    logger = get_service_logger('sync', 'operations', use_syslog=False, console_output=True)
    
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
                    notification_configs = parse_notification_args(
                        setup_config['notify_specs'],
                        notification_level=notification_level_from_state(
                            setup_config.get('notification_level'),
                            logger,
                        ),
                        strict_https=(
                            setup_config.get('notification_strict_https') is True
                        ),
                    )
                friendly_name = setup_config.get('friendly_name')
        except (ImportError, OSError, ValueError, KeyError, TypeError) as e:
            log_event(logger, "Failed to load notification configs", level=WARNING, error=str(e))
    
    log_event(logger, "Starting sync", source=source, destination=destination)
    start_time = datetime.now()
    
    # Initialize progress tracker
    progress_tracker = ProgressTracker(interval_seconds=PROGRESS_LOG_INTERVAL, logger=logger)
    
    # Track progress stats
    current_files = 0
    current_bytes = 0
    total_files = 0
    total_bytes = 0
    percent_done = 0
    
    try:
        # Run rsync with progress information
        process = subprocess.Popen(
            [
                '/usr/bin/rsync',
                '-av',
                '--delete',
                '--delete-delay',
                '--partial',
                '--mkpath',  # Create destination directory if it doesn't exist
                '--exclude=.git',
                '--info=progress2',  # Overall progress without per-file output
                '--stats',
                f'{source}/',
                f'{destination}/'
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1  # Line buffered
        )
        
        # Collect output for stats parsing
        stdout_lines = []
        stderr_lines = []
        
        # Type guard for stdout/stderr
        stdout: IO[str] = process.stdout  # type: ignore
        stderr: IO[str] = process.stderr  # type: ignore
        
        # Read output in real-time and log progress periodically
        while True:
            # Check if process has finished
            if process.poll() is not None:
                # Read any remaining output
                remaining_stdout = stdout.read()
                remaining_stderr = stderr.read()
                if remaining_stdout:
                    stdout_lines.extend(remaining_stdout.split('\n'))
                if remaining_stderr:
                    stderr_lines.extend(remaining_stderr.split('\n'))
                break
            
            # Use select to read available output without blocking
            readable, _, _ = select.select([stdout, stderr], [], [], 0.5)
            
            for stream in readable:
                line = stream.readline()
                if not line:
                    continue
                
                if stream == stdout:
                    stdout_lines.append(line.rstrip('\n'))
                    
                    # Parse progress2 format:
                    # "  1,234,567  45%  123.45MB/s    0:00:12"
                    # or with xfr#: "  1,234,567  45%  123.45MB/s    0:00:12  (xfr#9, to-chk=1234/5678)"
                    if '%' in line and 'B/s' in line:
                        try:
                            # Extract percentage
                            percent_match = re.search(r'(\d+)%', line)
                            if percent_match:
                                percent_done = int(percent_match.group(1))
                            
                            # Extract transferred bytes (first number before %)
                            bytes_match = re.search(r'^\s*([\d,]+)', line)
                            if bytes_match:
                                current_bytes = int(bytes_match.group(1).replace(',', ''))
                            
                            # Extract transfer count from (xfr#N, ...)
                            xfr_match = re.search(r'xfr#(\d+)', line)
                            if xfr_match:
                                current_files = int(xfr_match.group(1))
                            
                            # Extract to-check info: "to-chk=current/total"
                            chk_match = re.search(r'to-chk=(\d+)/(\d+)', line)
                            if chk_match:
                                remaining, total = chk_match.groups()
                                total_files = int(total)
                                # current_files can be calculated as total - remaining
                                if current_files == 0:  # Only use if xfr# not available
                                    current_files = total_files - int(remaining)
                        except (ValueError, AttributeError):
                            pass
                else:
                    stderr_lines.append(line.rstrip('\n'))
            
            # Log progress periodically using progress tracker
            if progress_tracker.should_log():
                msg = ProgressMessage("Progress").add_percentage(percent_done)
                
                if total_files > 0:
                    msg.add_files(current_files, total_files)
                elif current_files > 0:
                    msg.add_files(current_files)
                
                if current_bytes > 0:
                    msg.add_bytes(current_bytes)
                
                msg.add_duration(progress_tracker.get_elapsed_seconds())
                progress_tracker.force_log(msg.build())
        
        # Get exit code
        returncode = process.returncode
        
        if returncode != 0:
            # Rsync failed
            stderr_output = '\n'.join(stderr_lines)
            stdout_output = '\n'.join(stdout_lines)
            raise subprocess.CalledProcessError(
                returncode, 
                'rsync', 
                output=stdout_output, 
                stderr=stderr_output
            )
        
        duration = (datetime.now() - start_time).total_seconds()
        
        # Parse rsync stats for final summary
        stdout_output = '\n'.join(stdout_lines)
        files_transferred = 0
        total_size = 0
        
        for line in stdout_lines:
            if 'Number of files transferred:' in line:
                try:
                    files_transferred = int(line.split(':')[1].strip().replace(',', ''))
                except (IndexError, ValueError):
                    pass
            elif 'Total file size:' in line:
                try:
                    size_str = line.split(':')[1].strip().split()[0].replace(',', '')
                    total_size = int(size_str)
                except (IndexError, ValueError):
                    pass
        
        # Log final summary
        log_event(
            logger,
            "Sync completed",
            source=source,
            destination=destination,
            files_transferred=files_transferred,
            total_mb=total_size // BYTES_TO_MB,
            duration_seconds=f"{duration:.1f}",
        )
        
        # Send success notification
        if notification_configs:
            try:
                from lib.notifications import send_notification_safe
                name_prefix = f"[{friendly_name}] " if friendly_name else ""
                message = f"Synced {files_transferred} files"
                if total_size > 0:
                    message += f" ({total_size // BYTES_TO_MB} MB)"
                message += f" in {duration:.1f}s"
                
                details = f"""Sync Summary:
Source: {source}
Destination: {destination}
Files transferred: {files_transferred}
Total size: {total_size // BYTES_TO_MB} MB
Duration: {duration:.1f}s
"""
                
                send_notification_safe(
                    notification_configs,
                    subject=f"{name_prefix}Success: Sync completed",
                    job="sync",
                    status="good",
                    message=message,
                    details=details,
                    logger=logger,
                    event_type="sync.completed",
                    state="success",
                    dedup_key=f"sync:{source}:{destination}",
                    delivery_policy="signal",
                )
            except Exception as e:
                log_event(logger, "Failed to send success notification", level=ERROR, error=str(e))
        
        return 0
        
    except subprocess.CalledProcessError as e:
        duration = (datetime.now() - start_time).total_seconds()
        log_event(
            logger,
            "Sync failed",
            level=ERROR,
            source=source,
            destination=destination,
            duration_seconds=f"{duration:.1f}",
            exit_code=e.returncode,
        )
        error_output = e.stderr or e.output or str(e)
        log_event(logger, "Rsync error output", level=ERROR, stderr=error_output)
        
        # Send error notification
        if notification_configs:
            try:
                from lib.notifications import send_notification_safe
                name_prefix = f"[{friendly_name}] " if friendly_name else ""
                error_msg = e.stderr or e.stdout or str(e)
                send_notification_safe(
                    notification_configs,
                    subject=f"{name_prefix}Error: Sync failed",
                    job="sync",
                    status="error",
                    message=f"Sync failed: {source} -> {destination}",
                    details=f"Error:\n{error_msg}",
                    logger=logger,
                    event_type="sync.failed",
                    state="firing",
                    dedup_key=f"sync:{source}:{destination}",
                )
            except Exception as notify_error:
                log_event(logger, "Failed to send failure notification", level=ERROR, error=str(notify_error))
        
        return e.returncode
    
    except Exception as e:
        duration = (datetime.now() - start_time).total_seconds()
        log_event(
            logger,
            "Sync failed with unexpected error",
            level=ERROR,
            source=source,
            destination=destination,
            duration_seconds=f"{duration:.1f}",
            error=str(e),
        )
        
        # Send error notification
        if notification_configs:
            try:
                from lib.notifications import send_notification_safe
                name_prefix = f"[{friendly_name}] " if friendly_name else ""
                send_notification_safe(
                    notification_configs,
                    subject=f"{name_prefix}Error: Sync failed",
                    job="sync",
                    status="error",
                    message=f"Sync failed with unexpected error: {source} -> {destination}",
                    details=str(e),
                    logger=logger,
                    event_type="sync.failed",
                    state="firing",
                    dedup_key=f"sync:{source}:{destination}",
                )
            except Exception as notify_error:
                log_event(logger, "Failed to send failure notification", level=ERROR, error=str(notify_error))
        
        return 1


def main():
    """Main entry point."""
    if len(sys.argv) != 3:
        print("Usage: sync_rsync.py <source> <destination>")
        return 1
    
    source = sys.argv[1]
    destination = sys.argv[2]
    
    return run_rsync_with_notifications(source, destination)


if __name__ == '__main__':
    sys.exit(main())
