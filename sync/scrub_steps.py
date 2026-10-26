"""Data integrity checking with par2 and systemd timers."""

from __future__ import annotations

import os
import hashlib
from typing import Optional, Any

from lib.config import SetupConfig
from lib.remote_utils import run, is_package_installed
from lib.validation import (
    validate_filesystem_path, 
    validate_database_path, 
    validate_redundancy_percentage,
)
from lib.operation_log import create_operation_logger
from lib.task_utils import (
    validate_frequency,
)


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
        # Only lexical validation belongs in setup. Metadata operations, mount
        # checks, directory preparation, and disk metrics run in the worker.
        validate_filesystem_path(directory)
        validate_database_path(database_path)
        redundancy_value = validate_redundancy_percentage(redundancy)
        log_dir = "/var/log/scrub"
        scrub_id = hashlib.md5(f"{directory}:{database_path}".encode()).hexdigest()[:8]
        log_file = f"{log_dir}/scrub-{scrub_id}.log"
        # Setup reports its own final success or failure. Avoid reading and
        # notifying with stale target state before this run saves its config.
        from sync.service_tools.scrub_runner import run_scrub_supervised
        result = run_scrub_supervised(
            directory,
            database_path,
            redundancy_value,
            verify=False,
            config=config,
            log_dir=log_dir,
            log_file=log_file,
            suppress_notifications=True,
        )
        if result.get("ok") is not True or result.get("completed") is not True:
            raise RuntimeError(result.get("error") or "Initial par2 creation did not complete successfully")
        usage_metrics = (
            result.get("directory_disk_usage_percent"),
            result.get("database_disk_usage_percent"),
        )
        if any(type(value) is not int or not 0 <= value <= 100 for value in usage_metrics):
            raise RuntimeError("Initial PAR2 worker returned invalid disk usage metrics")

        logger.log_step("validation", "completed", "Scrub inputs and mounts are valid")
        logger.log_metric("validation_success", True)
        print(f"  ✓ Scrub spec validated: {directory}")
        print("  ℹ Performing initial par2 creation (fast mode)...")
        logger.log_step("initial_par2", "completed", "Initial par2 successful")
        logger.log_metric(
            "directory_disk_usage_percent", usage_metrics[0], "percent",
        )
        logger.log_metric(
            "database_disk_usage_percent", usage_metrics[1], "percent",
        )
        logger.complete("completed", "Scrub configured")

    except Exception as e:
        logger.log_error("scrub_setup_error", str(e))
        logger.complete("failed", f"Scrub setup failed: {e}")
        raise

    print(f"  ✓ Scrub configured: {directory} ({redundancy}, {frequency})")
