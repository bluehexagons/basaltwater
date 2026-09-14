"""Mount validation utilities for checking if paths are on mounted filesystems."""

from __future__ import annotations
import os
import subprocess
import time
import threading
import tempfile
from typing import Optional, Any, Callable


_MOUNT_PROBE_TIMEOUT_SECONDS = 15


def _run_mount_command(
    command: list[str],
    *,
    check: bool = False,
    text: bool = False,
) -> subprocess.CompletedProcess:
    """Run a fixed mount probe within a bounded diagnostic window."""

    return subprocess.run(
        command,
        capture_output=True,
        check=check,
        text=text,
        timeout=_MOUNT_PROBE_TIMEOUT_SECONDS,
    )


def is_path_under_mnt(path: str) -> bool:
    """Check if path is under /mnt directory."""
    return path == '/mnt' or path.startswith('/mnt/')


def get_mount_ancestor(path: str) -> Optional[str]:
    """Find the mount point ancestor of a path.
    
    Returns the path itself if it's a mount point, or the closest
    parent directory that is a mount point. Returns None if
    no mount point found.
    """
    current = path
    while current and current != '/':
        try:
            result = _run_mount_command(['mountpoint', '-q', current])
        except (OSError, subprocess.TimeoutExpired):
            return None
        if result.returncode == 0:
            return current
        current = os.path.dirname(current)
    return None


def is_path_mounted(path: str) -> bool:
    """Check if a path or its parent directories are on a mounted filesystem.
    
    Args:
        path: Path to check
        
    Returns:
        True if path is on a mounted filesystem, False otherwise
    """
    return get_mount_ancestor(path) is not None


def validate_mount_for_sync(path: str, path_name: str = "path") -> bool:
    """Validate that a path is mounted if it's under /mnt or should be mounted.
    
    Args:
        path: Path to validate
        path_name: Description of path for error messages (e.g., "source", "destination")
        
    Returns:
        True if validation passes, False otherwise
        
    Prints error messages to stderr if validation fails.
    """
    import sys
    
    # Check if path itself is a mount point
    try:
        result = _run_mount_command(['mountpoint', '-q', path])
    except (OSError, subprocess.TimeoutExpired):
        result = subprocess.CompletedProcess(['mountpoint', '-q', path], 124)
    
    if result.returncode == 0:
        # Path is a mount point, all good
        return True
    
    # Check if a parent directory is a mount point
    mount_ancestor = get_mount_ancestor(path)
    
    if mount_ancestor:
        # Parent is mounted, path is safe to use
        return True
    
    # No mount point found - this is an error if under /mnt
    if is_path_under_mnt(path):
        print(f"Error: {path_name.capitalize()} path {path} is not on a mounted filesystem", file=sys.stderr)
        return False
    
    # Not under /mnt and no mount requirement - allow it
    # (could be a local filesystem path)
    return True


def validate_smb_connectivity(path: str, *, writable: bool = True) -> bool:
    """Test actual SMB functionality for mounted paths.
    
    Args:
        path: Path to test (should be SMB mount)
        writable: Require writes for destinations; sources may be read-only.
        
    Returns:
        bool: True if SMB connectivity is working, False otherwise
    """
    if not is_path_mounted(path):
        print(f"Path is not mounted: {path}")
        return False
    
    # Check if this looks like an SMB mount
    try:
        result = _run_mount_command(
            ['findmnt', '-n', '-o', 'FSTYPE', '--target', path],
            text=True,
            check=True,
        )
        fstype = (result.stdout or '').strip()
        if fstype not in ['cifs', 'smb3', 'smb2']:
            print(f"Path {path} is not an SMB mount (type: {fstype})")
            return False
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        print(f"Could not determine filesystem type for {path}")
        return False
    
    # Test SMB-specific operations
    try:
        if writable:
            _probe_writable_directory(path)
        
        # Test directory listing (common SMB operation)
        # Optionally check a small listing to assert basic directory operations work
        with os.scandir(path) as entries:
            next(entries, None)
        print(f"SMB connectivity test passed for {path}")
        return True
        
    except (OSError, IOError, ValueError) as e:
        print(f"SMB connectivity test failed for {path}: {e}")
        return False


def _probe_writable_directory(path: str) -> None:
    """Probe an exclusively created temporary file, never a user's filename."""
    descriptor, probe = tempfile.mkstemp(prefix='.infra-tools-probe-', dir=path)
    try:
        with os.fdopen(descriptor, 'w+') as stream:
            stream.write('mount test')
            stream.flush()
            stream.seek(0)
            if stream.read() != 'mount test':
                raise ValueError('Mount probe content mismatch')
    finally:
        os.unlink(probe)


def get_mount_status_details(path: str) -> dict[str, Any]:
    """Get detailed mount status information.
    
    Args:
        path: Path to analyze
        
    Returns:
        Dict with detailed mount information
    """
    details: dict[str, Any] = {
        'path': path,
        'is_mounted': is_path_mounted(path),
        'mount_ancestor': get_mount_ancestor(path),
        'is_under_mnt': is_path_under_mnt(path),
        'fstype': None,
        'mount_options': None,
        'remote_server': None,
        'accessible': False
    }
    
    if not details['is_mounted']:
        return details
    
    try:
        # Get filesystem type and options
        result = _run_mount_command(
            ['findmnt', '-n', '-o', 'FSTYPE,OPTIONS', '--target', path],
            text=True,
            check=True,
        )
        parts = (result.stdout or '').strip().split()
        if len(parts) >= 1:
            details['fstype'] = parts[0]
        if len(parts) >= 2:
            details['mount_options'] = parts[1]
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        # If findmnt fails, leave fstype and options as None; details stay usable.
        pass
    
    # Check for remote server (SMB/NFS)
    if details['fstype'] in ['cifs', 'smb3', 'smb2', 'nfs', 'nfs4']:
        try:
            result = _run_mount_command(
                ['findmnt', '-n', '-o', 'SOURCE', '--target', path],
                text=True,
                check=True,
            )
            source = (result.stdout or '').strip()
            if '//' in source or ':' in source:
                details['remote_server'] = source
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            # If findmnt fails, leave remote_server as None.
            pass
    
    # Test accessibility
    try:
        # Status is read-only; writable connectivity is a separate probe.
        with os.scandir(path) as entries:
            next(entries, None)
        details['accessible'] = True
    except (OSError, IOError):
        details['accessible'] = False
    
    return details


def monitor_mount_with_callback(path: str, callback_func: Callable[[str], Any], check_interval: int = 10) -> threading.Thread:
    """Monitor mount status in background and call callback on issues.
    
    Args:
        path: Path to monitor
        callback_func: Function to call when mount issues detected
        check_interval: Check interval in seconds

    Returns:
        daemon Thread that is monitoring the mount
    """
    def monitor_loop():
        while True:
            if not validate_mount_for_sync(path, "monitored path"):
                try:
                    callback_func(f"Mount issue detected for {path}")
                except Exception as e:
                    print(f"Error in mount monitoring callback: {e}")
            time.sleep(check_interval)
    
    monitor_thread = threading.Thread(target=monitor_loop, daemon=True)
    monitor_thread.start()
    return monitor_thread


def validate_multiple_paths(paths: list[str], path_names: Optional[list[str]] = None) -> dict[str, bool]:
    """Validate multiple paths at once.
    
    Args:
        paths: List of paths to validate
        path_names: Optional list of descriptive names for paths
        
    Returns:
        Dict mapping path to validation result
    """
    if path_names is None:
        path_names = [f"path_{i}" for i in range(len(paths))]
    
    results: dict[str, bool] = {}
    for path, name in zip(paths, path_names):
        results[path] = validate_mount_for_sync(path, name)
    return results
