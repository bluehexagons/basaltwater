#!/usr/bin/env python3
"""Rsync wrapper with notification support for sync operations."""

from __future__ import annotations

import sys
import os
import subprocess
import re
import select
import codecs
import signal
import stat
import tempfile
import time
from collections import deque
from logging import ERROR, WARNING
from datetime import datetime

# Add lib directory to path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '../..'))

from lib.logging_utils import get_service_logger, log_event
from lib.progress_utils import ProgressTracker, ProgressMessage
from lib.task_utils import validate_sync_paths
from lib.validation import validate_filesystem_path

# Conversion constants
BYTES_TO_MB = 1024 * 1024

# Progress logging interval (seconds)
PROGRESS_LOG_INTERVAL = 30
RSYNC_TIMEOUT_SECONDS = 14400


def _output_lines(process):
    """Drain both pipes under one deadline, including the final process wait."""
    streams = {process.stdout: True, process.stderr: False}
    decoders = {stream: codecs.getincrementaldecoder("utf-8")(errors="replace") for stream in streams}
    pending = {stream: "" for stream in streams}
    deadline = time.monotonic() + RSYNC_TIMEOUT_SECONDS
    completed = False
    try:
        while streams:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired('rsync', RSYNC_TIMEOUT_SECONDS)
            readable, _, _ = select.select(list(streams), [], [], min(0.5, remaining))
            if not readable:
                yield True, ""
            for stream in readable:
                chunk = os.read(stream.fileno(), 65536)
                text = pending[stream] + decoders[stream].decode(chunk, final=not chunk)
                # rsync progress updates can use carriage returns instead of LF.
                lines = text.replace("\r", "\n").split("\n")
                pending[stream] = lines.pop()
                for line in lines:
                    yield streams[stream], line
                if not chunk or len(pending[stream]) >= 65536:
                    if pending[stream]:
                        yield streams[stream], pending[stream]
                    pending[stream] = ""
                if not chunk:
                    del streams[stream]
        process.wait(timeout=max(0, deadline - time.monotonic()))
        completed = True
    finally:
        try:
            if not completed:
                # rsync forks a local receiver too. Stop the entire private session,
                # even if its leader exited while a child still holds the pipes.
                def signal_group(sig):
                    try:
                        os.killpg(process.pid, sig)
                    except ProcessLookupError:
                        pass
                signal_group(signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
                finally:
                    signal_group(signal.SIGKILL)
                    process.wait(timeout=5)
        finally:
            process.stdout.close()
            process.stderr.close()


def _protected_paths(source: str, destination: str, paths: tuple[str, ...]) -> tuple[str, ...]:
    """Preserve a conflicting ancestor when a descendant cannot be excluded alone."""
    protected: dict[str, None] = {}
    for path in paths:
        validate_filesystem_path(path)
        if os.path.isabs(path) or path in ('', '.') or '..' in path.split('/'):
            raise ValueError(f"Expected a relative sync exclusion: {path}")
        path = os.path.normpath(path)
        components = path.split('/')
        for depth in range(1, len(components)):
            ancestor = '/'.join(components[:depth])
            conflict = False
            for endpoint in (source, destination):
                try:
                    mode = os.lstat(os.path.join(endpoint, ancestor)).st_mode
                except FileNotFoundError:
                    continue
                if not stat.S_ISDIR(mode):
                    conflict = True
                    break
            if conflict:
                path = ancestor
                break
        protected[path] = None
    return tuple(protected)


def _copy_new_conflicts(source: str, destination: str, protected: tuple[str, ...]) -> tuple[str, ...]:
    """Skip source directories blocked by existing destination files or links."""
    excluded = set(protected)
    conflicts: list[str] = []

    def fail(error: OSError) -> None:
        raise error

    for root, dirs, _files in os.walk(source, onerror=fail):
        for name in list(dirs):
            relative = os.path.relpath(os.path.join(root, name), source)
            # Pruning each excluded ancestor also avoids walking unavailable
            # nested mounts while preparing conservative copy mode.
            if relative in excluded or name in {'.git', '.rsync-partial'}:
                dirs.remove(name)
                continue
            try:
                source_mode = os.lstat(os.path.join(root, name)).st_mode
                destination_mode = os.lstat(os.path.join(destination, relative)).st_mode
            except FileNotFoundError:
                dirs.remove(name)  # A wholly new subtree has no conflicts.
                continue
            if stat.S_ISDIR(source_mode) and not stat.S_ISDIR(destination_mode):
                conflicts.append(relative)
                dirs.remove(name)
    return tuple(conflicts)


def run_rsync_with_notifications(
    source: str, destination: str, suppress_notifications: bool = False, *,
    excluded_paths: tuple[str, ...] = (), copy_new_only: bool = False,
) -> int:
    """Run rsync and send notifications on completion or failure.
    
    Args:
        source: Source directory
        destination: Destination directory
        suppress_notifications: If True, skip sending notifications (caller will handle)
        excluded_paths: Literal paths relative to the mirror root to preserve on both sides
        copy_new_only: Add new files without replacing or deleting existing destination data
        
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
    
    filter_file = None
    try:
        validate_sync_paths(source, destination)
        if not os.path.isdir(source):
            raise ValueError(f"Sync source is not an existing directory: {source}")
        protected_paths = _protected_paths(source, destination, excluded_paths)
        options = [] if copy_new_only else ['--delete', '--delete-delay']
        if copy_new_only:
            # Keep interrupted new files out of their final names so the next
            # --ignore-existing run can finish them instead of skipping them.
            options.extend(['--ignore-existing', '--partial-dir=.rsync-partial'])
            protected_paths += _copy_new_conflicts(source, destination, protected_paths)
        if protected_paths:
            # Use a private file to avoid argv limits for large reports. Anchored
            # excludes hide source paths and protect their destination copies,
            # including absent source files and replaced directory ancestors.
            filter_file = tempfile.NamedTemporaryFile(prefix='sync-excludes-')
            for path in protected_paths:
                if any(char in path for char in '*?['):
                    path = re.sub(r'([\\*?\[])', r'\\\1', path)
                filter_file.write(os.fsencode('/' + path) + b'\0')
            filter_file.flush()
            options.extend(['--from0', f'--exclude-from={filter_file.name}'])
        # Run rsync with progress information
        process = subprocess.Popen(
            [
                '/usr/bin/rsync',
                '-av',
                *options,
                '--partial',
                '--mkpath',  # Create destination directory if it doesn't exist
                '--exclude=.git',
                '--info=progress2',  # Overall progress without per-file output
                '--stats',
                '--',
                f'{source}/',
                f'{destination}/'
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
            bufsize=1  # Line buffered
        )
        
        # Collect output for stats parsing
        stdout_lines = deque(maxlen=4096)
        stderr_lines = deque(maxlen=256)
        
        # Read output in real-time and log progress periodically
        for is_stdout, line in _output_lines(process):
            if line:
                if is_stdout:
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
            if 'Number of files transferred:' in line or 'Number of regular files transferred:' in line:
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
        
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        returncode = 124 if isinstance(e, subprocess.TimeoutExpired) else e.returncode
        duration = (datetime.now() - start_time).total_seconds()
        log_event(
            logger,
            "Sync failed",
            level=ERROR,
            source=source,
            destination=destination,
            duration_seconds=f"{duration:.1f}",
            exit_code=returncode,
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
        
        return returncode
    
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
    finally:
        if filter_file is not None:
            filter_file.close()


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
