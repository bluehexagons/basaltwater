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
import json
from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from logging import ERROR, WARNING
from datetime import datetime
from typing import BinaryIO, TYPE_CHECKING

# Add lib directory to path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '../..'))

from lib.logging_utils import get_service_logger, log_event
from lib.progress_utils import ProgressTracker, ProgressMessage
from lib.task_utils import (
    check_path_on_smb_mount, configured_storage_mounts, ensure_directory,
    needs_mount_check, unavailable_sync_subtrees,
    validate_configured_storage_mounts, validate_sync_paths,
)
from lib.mount_utils import validate_smb_connectivity
from lib.runtime_config import RuntimeConfig
from lib.validation import validate_filesystem_path
from lib.validators import validate_username

if TYPE_CHECKING:
    from lib.config import SetupConfig

# Conversion constants
BYTES_TO_MB = 1024 * 1024

# Progress logging interval (seconds)
PROGRESS_LOG_INTERVAL = 30
RSYNC_TIMEOUT_SECONDS = 14400
WORKER_REPORT_LIMIT = 8192
WORKER_SAMPLE_PATH_LIMIT = 128


@dataclass
class SyncPrecautions:
    """Optional output collector; guarded partial syncs need a later retry."""

    warning: str = ""
    retry_required: bool = False


def _read_worker_report(stream: BinaryIO) -> SyncPrecautions:
    """Read a bounded, dedicated report, never rsync's ordinary output."""
    stream.seek(0)
    payload = stream.read(WORKER_REPORT_LIMIT + 1)
    if len(payload) > WORKER_REPORT_LIMIT:
        raise ValueError('Sync worker report exceeds size limit')
    report = json.loads(payload)
    if (not isinstance(report, dict) or set(report) != {'version', 'unavailable_count', 'sample'}
            or type(report['version']) is not int or report['version'] != 1
            or type(report['unavailable_count']) is not int or report['unavailable_count'] < 0
            or not isinstance(report['sample'], list)
            or len(report['sample']) != min(report['unavailable_count'], 5)
            or any(not isinstance(path, str) or not path or len(path) > WORKER_SAMPLE_PATH_LIMIT
                   for path in report['sample'])):
        raise ValueError('Invalid sync worker report')
    count = report['unavailable_count']
    warning = (f"Preserved {count} subtree(s) on unavailable configured mounts: "
               f"{', '.join(report['sample'])}; sync incomplete, retry needed") if count else ""
    return SyncPrecautions(warning, bool(count))


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


@contextmanager
def _rsync_command(source: str, destination: str, excluded_paths: tuple[str, ...],
                   copy_new_only: bool) -> Iterator[tuple[list[str], BinaryIO | None]]:
    """Build filters inside the pinned mount view and retain them through exec."""
    protected_paths = _protected_paths(source, destination, excluded_paths)
    options = ['--partial-dir=.rsync-partial']
    if not copy_new_only:
        options.extend(['--delete', '--delete-delay'])
    if copy_new_only:
        options.append('--ignore-existing')
        protected_paths += _copy_new_conflicts(source, destination, protected_paths)
    with tempfile.TemporaryFile() as filters:
        if protected_paths:
            for path in protected_paths:
                if any(char in path for char in '*?['):
                    path = re.sub(r'([\\*?\[])', r'\\\1', path)
                filters.write(os.fsencode('/' + path) + b'\0')
            filters.flush()
            filters.seek(0)
            options.extend(['--from0', '--exclude-from=-'])
        yield [
            '/usr/bin/rsync', '-av', *options, '--partial', '--mkpath',
            '--exclude=.git', '--exclude=.rsync-partial', '--info=progress2', '--stats', '--',
            f'{source}/', f'{destination}/',
        ], filters if protected_paths else None


def _mount_worker(report_fd: int) -> int:
    """Validate and exec only after unshare has detached mount propagation."""
    try:
        request = json.load(sys.stdin)
        if os.readlink('/proc/self/ns/mnt') == os.readlink(f'/proc/{os.getppid()}/ns/mnt'):
            raise RuntimeError('Sync worker requires a new private mount namespace')
        mountpoints: set[str] = set()
        with open('/proc/self/mountinfo', encoding='utf-8') as mounts:
            for line in mounts:
                mount, filesystem = line.split(' - ', 1)
                fields = mount.split()
                optional = fields[6:]
                if any(field.startswith(('shared:', 'master:', 'propagate_from:')) for field in optional):
                    raise RuntimeError('Sync worker mount propagation is not private')
                if filesystem.split()[0] != 'autofs':
                    # mountinfo escapes whitespace and backslashes in paths.
                    mountpoints.add(re.sub(r'\\([0-7]{3})', lambda match: chr(int(match[1], 8)), fields[4]))
        if not mountpoints:
            raise RuntimeError('Cannot verify private mount table')
        mounted_roots = frozenset(mountpoints)

        source, destination = request['source'], request['destination']
        config = RuntimeConfig.from_dict({
            'storage_mounts': [['', root] for root in request['mounts']],
            'smb_mounts': [[root] for root in request['smb_mounts']],
        })
        validate_sync_paths(source, destination)
        for path, label in ((source, 'source'), (destination, 'destination')):
            validate_configured_storage_mounts(path, config, mounted_roots=mounted_roots)
            resolved = os.path.realpath(path)
            needs_mount = needs_mount_check(path, config) or needs_mount_check(resolved, config)
            if needs_mount and not any(root != '/' and os.path.commonpath([resolved, root]) == root
                                       for root in mounted_roots):
                raise RuntimeError(f'Required {label} mount is unavailable: {path}')
        if not os.path.isdir(source):
            raise ValueError(f'Sync source is not an existing directory: {source}')
        username = request['setup_username']
        if username is not None:
            if not validate_username(username):
                raise ValueError('Invalid initial sync username')
            ensure_directory(source, username)
            ensure_directory(os.path.dirname(destination), username)
            if check_path_on_smb_mount(source, config) and not validate_smb_connectivity(source, writable=False):
                raise RuntimeError(f'Source SMB connectivity failed: {source}')
            if check_path_on_smb_mount(destination, config):
                ensure_directory(destination, username)
                if not validate_smb_connectivity(destination):
                    raise RuntimeError(f'Destination SMB connectivity failed: {destination}')
        unavailable = unavailable_sync_subtrees(source, destination, config, mounted_roots=mounted_roots)
        if unavailable:
            print(f'Preserving unavailable mount subtrees: {", ".join(unavailable[:5])}', file=sys.stderr)
        excluded = tuple(dict.fromkeys([*request['excluded_paths'], *unavailable]))
        with _rsync_command(source, destination, excluded, request['copy_new_only']) as (command, filters):
            # The anonymous report is separate from rsync output and closed
            # before exec. Bound the sample even for very long declarations.
            report = json.dumps({'version': 1, 'unavailable_count': len(unavailable),
                                 'sample': [path[:WORKER_SAMPLE_PATH_LIMIT] for path in unavailable[:5]]}).encode('utf-8')
            if len(report) > WORKER_REPORT_LIMIT:
                raise ValueError('Sync worker report exceeds size limit')
            with os.fdopen(report_fd, 'wb') as stream:
                stream.write(report)
            # No extra supervisor: rsync retains the parent's private session,
            # pipes and deadline, and this namespace for its entire traversal.
            if filters is not None:
                # JSON has been fully consumed. Rsync closes nonstandard FDs
                # during startup, so give it the anonymous NUL filters on stdin.
                os.dup2(filters.fileno(), 0)
            os.execv(command[0], command)
    except Exception as exc:
        print(f'Sync mount worker refused to run: {exc}', file=sys.stderr)
        return 1
    return 0


def _start_rsync(source: str, destination: str, config: SetupConfig | RuntimeConfig,
                 excluded_paths: tuple[str, ...], copy_new_only: bool,
                 initial_setup: bool, report: BinaryIO) -> subprocess.Popen[str]:
    """Transfer declarations, never credentials, to a namespace-isolated worker."""
    request = {
        'source': source, 'destination': destination,
        'mounts': configured_storage_mounts(config),
        'smb_mounts': [spec[0] for spec in getattr(config, 'smb_mounts', None) or []],
        'setup_username': config.username if initial_setup else None,
        'excluded_paths': excluded_paths, 'copy_new_only': copy_new_only,
    }
    # A regular anonymous file avoids argv limits and pipe writes that could
    # block outside the output reader's deadline for large exclusion reports.
    with tempfile.TemporaryFile(mode='w+', encoding='utf-8') as stream:
        json.dump(request, stream)
        stream.seek(0)
        try:
            return subprocess.Popen(
                ['/usr/bin/unshare', '--mount', '--propagation', 'private', '--',
                 sys.executable, '-u', os.path.abspath(__file__), '--mount-worker', str(report.fileno())],
                stdin=stream, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, start_new_session=True, bufsize=1,
                pass_fds=(report.fileno(),),
            )
        except OSError as exc:
            raise RuntimeError('Cannot start private mount namespace; sync requires '
                               'util-linux unshare and CAP_SYS_ADMIN (normally root)') from exc


def run_rsync_with_notifications(
    source: str, destination: str, suppress_notifications: bool = False, *,
    excluded_paths: tuple[str, ...] = (), copy_new_only: bool = False,
    config: SetupConfig | RuntimeConfig | None = None,
    initial_setup: bool = False,
    precautions: SyncPrecautions | None = None,
) -> int:
    """Run rsync and send notifications on completion or failure.
    
    Args:
        source: Source directory
        destination: Destination directory
        suppress_notifications: If True, skip sending notifications (caller will handle)
        excluded_paths: Literal paths relative to the mirror root to preserve on both sides
        copy_new_only: Add new files without replacing or deleting existing destination data
        config: Current declarations for setup/runtime; None strictly loads saved state
        initial_setup: Prepare directories and probe SMB within the private namespace
        precautions: Optional collector for worker warnings and incomplete mount coverage
        
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
    
    report = None
    if precautions is None:
        precautions = SyncPrecautions()
    precautions.warning, precautions.retry_required = "", False
    try:
        if config is None:
            from lib.machine_state import load_setup_config
            saved_config = load_setup_config()
            if saved_config is None:
                raise RuntimeError('Saved setup configuration is required for standalone sync mount guards')
            config = RuntimeConfig.from_dict(saved_config)
        validate_sync_paths(source, destination)
        if not os.path.isdir(source):
            raise ValueError(f"Sync source is not an existing directory: {source}")
        report = tempfile.TemporaryFile()
        process = _start_rsync(source, destination, config, excluded_paths, copy_new_only, initial_setup, report)
        
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
            if 'unshare:' in stderr_output:
                stderr_output += ('\nPrivate mount namespace unavailable; sync refused. '
                                  'Requires CAP_SYS_ADMIN (normally root); check container/service restrictions.')
            raise subprocess.CalledProcessError(
                returncode, 
                'rsync', 
                output=stdout_output, 
                stderr=stderr_output
            )
        worker_precautions = _read_worker_report(report)
        precautions.warning = worker_precautions.warning
        precautions.retry_required = worker_precautions.retry_required
        if precautions.warning:
            log_event(logger, 'Sync completed with precautions', level=WARNING, warning=precautions.warning)
        if stderr_lines:
            log_event(logger, 'Sync worker warnings', level=WARNING, stderr='\n'.join(stderr_lines))
        
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
                if precautions.warning:
                    details += f"\nWarning: {precautions.warning}\n"
                
                send_notification_safe(
                    notification_configs,
                    subject=f"{name_prefix}Sync completed with warnings" if precautions.warning else f"{name_prefix}Success: Sync completed",
                    job="sync",
                    status="warning" if precautions.warning else "good",
                    message=message,
                    details=details,
                    logger=logger,
                    event_type="sync.completed",
                    state="firing" if precautions.warning else "success",
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
        if report is not None:
            report.close()


def main():
    """Main entry point."""
    if len(sys.argv) == 3 and sys.argv[1] == '--mount-worker':
        return _mount_worker(int(sys.argv[2]))
    if len(sys.argv) != 3:
        print("Usage: sync_rsync.py <source> <destination>")
        return 1
    
    source = sys.argv[1]
    destination = sys.argv[2]
    
    return run_rsync_with_notifications(source, destination)


if __name__ == '__main__':
    sys.exit(main())
