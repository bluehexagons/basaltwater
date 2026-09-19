"""Data integrity checking with par2 and systemd timers."""

from __future__ import annotations

import os
import hashlib
from typing import Optional, Any

from lib.config import SetupConfig
from lib.remote_utils import run, is_package_installed
from lib.mount_utils import validate_mount_for_sync, validate_smb_connectivity
from lib.disk_utils import get_disk_usage_details
from lib.validation import (
    validate_filesystem_path, 
    validate_database_path, 
    validate_redundancy_percentage,
)
from lib.operation_log import create_operation_logger
from lib.task_utils import (
    validate_frequency,
    check_path_on_smb_mount,
    ensure_directory
)
from lib.task_utils import validate_configured_storage_mounts


def install_par2(config: SetupConfig) -> None:
    if is_package_installed("par2"):
        print("  ✓ par2 already installed")
        return
    
    os.environ["DEBIAN_FRONTEND"] = "noninteractive"
    run(["apt-get", "install", "-y", "-qq", "par2"])
    print("  ✓ par2 installed")

def parse_scrub_spec(scrub_spec: list[str]) -> dict[str, Any]:
    if len(scrub_spec) != 4:
        raise ValueError(f"Invalid scrub spec: expected 4 arguments, got {len(scrub_spec)}")
    
    directory, database_path, redundancy, frequency = scrub_spec
    
    if not directory.startswith('/'):
        raise ValueError(f"Directory path must be absolute: {directory}")
    
    if not database_path.startswith('/'):
        database_path = os.path.join(directory, database_path)
    
    database_path = os.path.normpath(database_path)
    
    if not redundancy.endswith('%'):
        raise ValueError(f"Redundancy must end with '%': {redundancy}")
    try:
        redundancy_value = int(redundancy[:-1])
        if redundancy_value < 1 or redundancy_value > 100:
            raise ValueError(f"Redundancy percentage must be between 1 and 100: {redundancy}")
    except ValueError as e:
        if "invalid literal" in str(e):
            raise ValueError(f"Invalid redundancy format: {redundancy}")
        raise
    
    validate_frequency(frequency)
    
    return {
        'directory': directory,
        'database_path': database_path,
        'redundancy': redundancy,
        'frequency': frequency
    }

def create_scrub_service(config: SetupConfig, scrub_spec: Optional[list[str]] = None, **_ : Any) -> None:
    if not scrub_spec:
        raise ValueError("scrub_spec is required")
    
    scrub_config = parse_scrub_spec(scrub_spec)
    
    directory: str = scrub_config['directory']
    database_path: str = scrub_config['database_path']
    redundancy: str = scrub_config['redundancy']
    frequency: str = scrub_config['frequency']
    logger = create_operation_logger("scrub", directory=directory, database_path=database_path, 
                                    redundancy=redundancy, frequency=frequency)

    try:
        logger.log_step("validation", "started", "Validating scrub paths, mounts, and redundancy")
        validate_filesystem_path(directory, must_exist=True, check_writable=False)
        validate_database_path(database_path)
        for path in (directory, database_path):
            validate_configured_storage_mounts(path, config)
        if not validate_mount_for_sync(directory, "directory"):
            raise RuntimeError(f"Required source mount is unavailable: {directory}")
        if not validate_mount_for_sync(database_path, "database"):
            raise RuntimeError(f"Required database mount is unavailable: {database_path}")
        redundancy_value = validate_redundancy_percentage(redundancy)
        logger.log_step("validation", "completed", "Scrub inputs are valid")
        logger.log_metric("validation_success", True)

        ensure_directory(directory, config.username)

        db_parent = os.path.dirname(database_path)
        if db_parent:
            ensure_directory(db_parent, config.username)

        dir_on_smb = check_path_on_smb_mount(directory, config)
        db_on_smb = check_path_on_smb_mount(database_path, config)

        if dir_on_smb or db_on_smb:
            logger.log_step("mount_validation_enhanced", "started", "Performing enhanced mount validation")
            if dir_on_smb:
                if not validate_smb_connectivity(directory):
                    raise RuntimeError(f"Source SMB connectivity failed: {directory}")
            if db_on_smb:
                if not validate_smb_connectivity(database_path):
                    raise RuntimeError(f"Database SMB connectivity failed: {database_path}")
            logger.log_step("mount_validation_enhanced", "completed", "Enhanced mount validation completed")

        print(f"  ✓ Scrub spec validated: {directory}")
        print("  ℹ Performing initial par2 creation (fast mode)...")

        from sync.service_tools.scrub_par2 import scrub_directory
        log_dir = "/var/log/scrub"
        os.makedirs(log_dir, exist_ok=True)
        scrub_id = hashlib.md5(f"{directory}:{database_path}".encode()).hexdigest()[:8]
        log_file = f"{log_dir}/scrub-{scrub_id}.log"
        # Setup reports its own final success or failure. Avoid reading and
        # notifying with stale target state before this run saves its config.
        result = scrub_directory(
            directory,
            database_path,
            redundancy_value,
            log_file,
            verify=False,
            suppress_notifications=True,
        )
        if not result.get("ok", False):
            raise RuntimeError("Initial par2 creation did not complete successfully")

        logger.log_step("initial_par2", "completed", "Initial par2 successful")

        dir_disk = get_disk_usage_details(directory)
        db_disk = get_disk_usage_details(os.path.dirname(database_path))
        logger.log_metric("directory_disk_usage_percent", dir_disk['usage_percent'], "percent")
        logger.log_metric("database_disk_usage_percent", db_disk['usage_percent'], "percent")
        logger.complete("completed", "Scrub configured")

    except Exception as e:
        logger.log_error("scrub_setup_error", str(e))
        logger.complete("failed", f"Scrub setup failed: {e}")
        raise

    print(f"  ✓ Scrub configured: {directory} ({redundancy}, {frequency})")
