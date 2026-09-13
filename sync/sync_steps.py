"""Directory synchronization with rsync and systemd timers."""

from __future__ import annotations

import os
from typing import Optional, Any

from lib.config import SetupConfig
from lib.remote_utils import run, is_package_installed
from lib.mount_utils import validate_mount_for_sync, validate_smb_connectivity
from lib.disk_utils import get_disk_usage_details
from lib.validation import validate_filesystem_path
from lib.operation_log import create_operation_logger
from lib.task_utils import (
    validate_frequency,
    check_path_on_smb_mount,
    ensure_directory
)


def install_rsync(config: SetupConfig) -> None:
    if is_package_installed("rsync"):
        print("  ✓ rsync already installed")
        return
    
    os.environ["DEBIAN_FRONTEND"] = "noninteractive"
    run(["apt-get", "install", "-y", "-qq", "rsync"])
    print("  ✓ rsync installed")

def parse_sync_spec(sync_spec: list[str]) -> dict[str, Any]:
    if len(sync_spec) != 3:
        raise ValueError(f"Invalid sync spec: expected 3 arguments, got {len(sync_spec)}")
    
    source, destination, interval = sync_spec
    
    if not source.startswith('/'):
        raise ValueError(f"Source path must be absolute: {source}")
    if not destination.startswith('/'):
        raise ValueError(f"Destination path must be absolute: {destination}")
    
    validate_frequency(interval, "interval")
    
    return {
        'source': source,
        'destination': destination,
        'interval': interval
    }

def create_sync_service(config: SetupConfig, sync_spec: Optional[list[str]] = None, **_ : Any) -> None:
    if not sync_spec:
        raise ValueError("sync_spec is required")
    
    sync_config = parse_sync_spec(sync_spec)
    
    source = sync_config['source']
    destination = sync_config['destination']
    interval = sync_config['interval']
    
    logger = create_operation_logger("sync", source=source, destination=destination, interval=interval)

    try:
        logger.log_step("validation", "started", "Validating sync paths and mounts")
        validate_filesystem_path(source, must_exist=True, check_writable=False)
        validate_filesystem_path(destination, check_writable=True)
        if not validate_mount_for_sync(source, "source"):
            raise RuntimeError(f"Required source mount is unavailable: {source}")
        if not validate_mount_for_sync(destination, "destination"):
            raise RuntimeError(f"Required destination mount is unavailable: {destination}")
        logger.log_step("validation", "completed", "Sync paths and mounts are valid")
        logger.log_metric("validation_success", True)

        ensure_directory(source, config.username)

        dest_parent = os.path.dirname(destination)
        if dest_parent:
            ensure_directory(dest_parent, config.username)

        source_on_smb = check_path_on_smb_mount(source, config)
        dest_on_smb = check_path_on_smb_mount(destination, config)

        if source_on_smb or dest_on_smb:
            logger.log_step("mount_validation_enhanced", "started", "Performing enhanced mount validation")
            if source_on_smb:
                if not validate_smb_connectivity(source):
                    raise RuntimeError(f"Source SMB connectivity failed: {source}")
            if dest_on_smb:
                if not validate_smb_connectivity(destination):
                    raise RuntimeError(f"Destination SMB connectivity failed: {destination}")
            logger.log_step("mount_validation_enhanced", "completed", "Enhanced mount validation completed")

        print(f"  ✓ Sync spec validated: {source} → {destination}")
        print("  ℹ Performing initial sync...")

        from sync.service_tools.sync_rsync import run_rsync_with_notifications
        # Setup reports its own final success or failure. Avoid reading and
        # notifying with stale target state before this run saves its config.
        result = run_rsync_with_notifications(
            source,
            destination,
            suppress_notifications=True,
        )
        if result != 0:
            raise RuntimeError(f"Initial sync failed with exit code {result}")

        logger.log_step("initial_sync", "completed", "Initial sync successful")

        source_disk = get_disk_usage_details(source)
        dest_disk = get_disk_usage_details(destination)
        logger.log_metric("source_disk_usage_percent", source_disk['usage_percent'], "percent")
        logger.log_metric("destination_disk_usage_percent", dest_disk['usage_percent'], "percent")
        logger.complete("completed", "Sync configured")

    except Exception as e:
        logger.log_error("sync_setup_error", str(e))
        logger.complete("failed", f"Sync setup failed: {e}")
        raise

    print(f"  ✓ Sync configured: {source} → {destination} ({interval})")
