"""Directory synchronization with rsync and systemd timers."""

from __future__ import annotations

import os
from typing import Optional, Any

from lib.config import SetupConfig
from lib.remote_utils import run, is_package_installed
from lib.operation_log import create_operation_logger
from lib.task_utils import validate_frequency, validate_sync_paths


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
    validate_sync_paths(source, destination, resolve=False)
    
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
        # Parsing above is lexical. All filesystem validation and preparation
        # belongs to the supervised worker, including cold CIFS metadata.
        logger.log_step("validation", "started", "Validating sync paths and mounts in worker")
        print("  ℹ Performing initial sync...")

        from sync.service_tools.sync_rsync import SyncPrecautions, run_rsync_with_notifications
        # Setup reports its own final success or failure. Avoid reading and
        # notifying with stale target state before this run saves its config.
        precautions = SyncPrecautions()
        result = run_rsync_with_notifications(
            source,
            destination,
            suppress_notifications=True,
            config=config,
            initial_setup=True,
            precautions=precautions,
        )
        if result != 0:
            raise RuntimeError(f"Initial sync failed with exit code {result}")
        if precautions.retry_required:
            raise RuntimeError(f"Initial sync incomplete: {precautions.warning}")

        logger.log_step("validation", "completed", "Sync paths and mounts are valid")
        logger.log_metric("validation_success", True)
        logger.log_step("initial_sync", "completed", "Initial sync successful")
        logger.complete("completed", "Sync configured")

    except Exception as e:
        logger.log_error("sync_setup_error", str(e))
        logger.complete("failed", f"Sync setup failed: {e}")
        raise

    print(f"  ✓ Sync configured: {source} → {destination} ({interval})")
