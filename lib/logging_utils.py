"""Centralized logging system for infra tools services.

This module provides a unified logging infrastructure for all infra_tools services
and systems. All errors and warnings are logged in a standardized format that can be
easily monitored by external monitoring systems.

Key Features:
- Rotating file handlers with configurable size and backup count
- Configurable log levels (DEBUG, INFO, WARNING, ERROR, CRITICAL)
- Structured logging format with timestamps and severity levels
- Centralized log directory (/var/log/infra_tools/)
- Automatic fallback to stderr if file logging fails
- Service-specific loggers with consistent configuration
- Test mode: Set INFRA_TOOLS_TEST=1 to disable console and syslog output
"""

from __future__ import annotations

import os
from logging import (
    Formatter,
    INFO,
    Logger,
    NullHandler,
    StreamHandler,
    WARNING,
    getLogger,
)
from logging.handlers import RotatingFileHandler, SysLogHandler
from pathlib import Path
from typing import Optional
import sys
import subprocess
from lib.types import BYTES_PER_MB

# Default log configuration
DEFAULT_LOG_MAX_BYTES = 5 * BYTES_PER_MB  # 5 MB
DEFAULT_LOG_BACKUP_COUNT = 5
DEFAULT_LOG_LEVEL = INFO
DEFAULT_LOG_DIR = "/var/log/infra_tools"

# Evaluate test mode when handlers are configured rather than only when this
# module is imported. This keeps test runners quiet if they set the environment
# after importing application modules.
TEST_MODE_ENV_VAR = "INFRA_TOOLS_TEST"

# Standard log format for all services
# Format: timestamp - severity - service - message
STANDARD_LOG_FORMAT = "%(asctime)s - %(levelname)-8s - %(name)s - %(message)s"
STANDARD_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def _test_mode_enabled() -> bool:
    """Return whether console logging should be suppressed for test runs."""

    return os.environ.get(TEST_MODE_ENV_VAR, "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _report_logging_error(message: str) -> None:
    """Report logger setup errors without polluting test output."""

    if not _test_mode_enabled():
        print(message, file=sys.stderr)


def _suppress_test_console_handlers(logger: Logger) -> None:
    """Remove console handlers left behind before a test run was enabled."""

    if not _test_mode_enabled():
        return
    for handler in list(logger.handlers):
        if isinstance(handler, StreamHandler) and not isinstance(
            handler, RotatingFileHandler
        ):
            logger.removeHandler(handler)
            handler.close()


def _ensure_fallback_handler(logger: Logger, level: int = INFO) -> None:
    """Add a fallback handler if no handlers are configured.
    
    Args:
        logger: Logger instance to add fallback handler to
        level: Log level for the handler
    """
    _suppress_test_console_handlers(logger)
    if logger.handlers:
        return

    handler = NullHandler() if _test_mode_enabled() else StreamHandler(sys.stderr)
    handler.setLevel(level)
    handler.setFormatter(Formatter(STANDARD_LOG_FORMAT, STANDARD_DATE_FORMAT))
    logger.addHandler(handler)


def _journal_stream_available() -> bool:
    """Return whether systemd is already collecting this process's output."""

    return bool(os.environ.get("JOURNAL_STREAM"))


def _remove_stream_handlers(logger: Logger) -> None:
    """Remove fallback stream handlers before installing one journal stream."""

    for handler in list(logger.handlers):
        if isinstance(handler, StreamHandler) and not isinstance(
            handler, RotatingFileHandler
        ):
            logger.removeHandler(handler)
            handler.close()


def _can_write_log_path(log_path: Path) -> bool:
    """Return true when the current process should try file logging there."""
    try:
        if log_path.is_relative_to(Path("/var/log")) and hasattr(os, "geteuid") and os.geteuid() != 0:
            return False
    except ValueError:
        pass
    return True


def get_standard_formatter() -> Formatter:
    """Get the standard formatter for all infra_tools logs.
    
    Returns:
        Configured Formatter instance
    """
    return Formatter(STANDARD_LOG_FORMAT, STANDARD_DATE_FORMAT)


def get_rotating_logger(
    name: str,
    log_file: str,
    max_bytes: int = DEFAULT_LOG_MAX_BYTES,
    backup_count: int = DEFAULT_LOG_BACKUP_COUNT,
    level: int = DEFAULT_LOG_LEVEL
) -> Logger:
    """Return a logger configured with a rotating file handler.
    
    Args:
        name: Logger name (typically service name)
        log_file: Path to log file
        max_bytes: Maximum size of log file before rotation
        backup_count: Number of backup files to keep
        level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        
    Returns:
        Configured Logger instance with rotating file handler
    """
    logger = getLogger(name)
    _suppress_test_console_handlers(logger)
    if not logger.handlers:
        logger.setLevel(level)
        logger.propagate = False

    log_path = Path(log_file)
    if not _can_write_log_path(log_path):
        _ensure_fallback_handler(logger, level)
        return logger

    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
    except (OSError, IOError) as e:
        _report_logging_error(f"Error creating log directory {log_path.parent}: {e}")
        _ensure_fallback_handler(logger, level)
        return logger

    log_file_path = str(log_path.resolve())
    for handler in logger.handlers:
        if isinstance(handler, RotatingFileHandler) and handler.baseFilename == log_file_path:
            return logger

    try:
        handler = RotatingFileHandler(log_file_path, maxBytes=max_bytes, backupCount=backup_count)
        handler.setLevel(level)
        handler.setFormatter(get_standard_formatter())
        logger.addHandler(handler)
    except (OSError, IOError) as e:
        _report_logging_error(f"Error opening log file {log_file_path}: {e}")
        _ensure_fallback_handler(logger, level)
        return logger

    return logger


def get_service_logger(
    service_name: str,
    log_subdir: Optional[str] = None,
    level: int = DEFAULT_LOG_LEVEL,
    use_syslog: bool = False,
    console_output: bool = True
) -> Logger:
    """Get a logger configured for a specific service.
    
    This is the recommended way to get a logger for infra_tools services.
    It automatically sets up the logger with:
    - Standard formatting
    - Rotating file handler
    - Optional syslog integration
    - Optional console output
    - Centralized log directory
    
    Args:
        service_name: Name of the service (e.g., 'scrub_par2', 'auto_update_node')
        log_subdir: Optional subdirectory under DEFAULT_LOG_DIR (e.g., 'scrub', 'web')
        level: Logging level
        use_syslog: Whether to also send logs to syslog
        console_output: Whether to also print to console (default: True)
        
    Returns:
        Configured Logger instance
        
    Example:
        logger = get_service_logger('auto_update_node', 'web')
        logger.info('Starting Node.js update')
        logger.warning('npm update failed, retrying')
        logger.error('Update failed after retries')
    """
    log_dir = Path(DEFAULT_LOG_DIR)
    if log_subdir:
        log_dir = log_dir / log_subdir
    log_file = log_dir / f"{service_name}.log"
    
    # In test mode, disable console output (use local variable, don't mutate param)
    effective_console_output = console_output and not _test_mode_enabled()
    
    logger = get_rotating_logger(service_name, str(log_file), level=level)

    if effective_console_output:
        if _journal_stream_available():
            # A non-root systemd service cannot open /var/log directly and
            # receives a stderr fallback from get_rotating_logger().  Remove
            # that fallback before adding stdout so journald sees one record.
            _remove_stream_handlers(logger)
        # Check if stdout console handler already exists (explicit loop helps type-checkers)
        has_console = False
        for h in logger.handlers:
            if isinstance(h, StreamHandler):
                try:
                    # Accessing `.stream` is runtime-safe but the type checker
                    # may not know about this attribute on all handlers.
                    if h.stream is sys.stdout:  # type: ignore[attr-defined]
                        has_console = True
                        break
                except AttributeError:
                    # If handler doesn't expose `stream`, skip it
                    continue
        if not has_console:
            console_handler = StreamHandler(sys.stdout)
            console_handler.setLevel(level)
            # Use simpler format for console: just the message with level prefix
            console_formatter = Formatter('%(message)s')
            console_handler.setFormatter(console_formatter)
            logger.addHandler(console_handler)
    
    if use_syslog and not _test_mode_enabled() and not _journal_stream_available():
        try:
            syslog_handler = SysLogHandler(address='/dev/log')
            syslog_handler.setLevel(level)
            syslog_handler.setFormatter(Formatter(f'{service_name}: %(message)s'))
            logger.addHandler(syslog_handler)
        except (OSError, IOError):
            # Syslog not available, continue without it
            pass
    
    return logger


def log_message(logger: Logger, message: str, level: int = INFO) -> None:
    """Write a log message with graceful error handling.
    
    Args:
        logger: Logger instance to use
        message: Message to log
        level: Log level (INFO, WARNING, ERROR, etc.)
    """
    try:
        logger.log(level, message)
    except (OSError, IOError) as e:
        log_target = "unknown log"
        for handler in logger.handlers:
            if isinstance(handler, RotatingFileHandler):
                log_target = handler.baseFilename
                break
        print(f"Error writing to log {log_target}: {e}", file=sys.stderr)


def log_event(logger: Logger, message: str, level: int = INFO, **context: object) -> None:
    """Log a message with stable key=value context for service diagnostics."""

    context_parts = [
        f"{key}={value!r}"
        for key, value in sorted(context.items())
        if value is not None
    ]
    rendered_message = message
    if context_parts:
        rendered_message = f"{message} | {' '.join(context_parts)}"
    log_message(logger, rendered_message, level=level)


def ensure_log_directory(subdir: Optional[str] = None) -> Path:
    """Ensure the log directory exists and return its path.
    
    Args:
        subdir: Optional subdirectory under DEFAULT_LOG_DIR
        
    Returns:
        Path to the log directory
    """
    log_dir = Path(DEFAULT_LOG_DIR)
    if subdir:
        log_dir = log_dir / subdir
    
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except (OSError, IOError) as e:
        _report_logging_error(f"Error creating log directory {log_dir}: {e}")
    
    return log_dir


def log_subprocess_result(
    logger: Logger,
    action: str,
    result: subprocess.CompletedProcess[str],
    success_level: int = INFO,
    failure_level: int = WARNING
) -> bool:
    """Log concise command result details and return success state."""
    if result.returncode == 0:
        logger.log(success_level, f"✓ {action}")
        return True

    stderr_raw = result.stderr or ""
    if isinstance(stderr_raw, bytes):
        stderr_raw = stderr_raw.decode(errors="replace")
    stderr = stderr_raw.strip().splitlines()
    if stderr:
        detail_lines = stderr[:3]
        details = " | ".join(detail_lines)
        if len(stderr) > 3:
            details += " | ..."
    else:
        details = f"exit code {result.returncode}"
    logger.log(failure_level, f"⚠ {action} failed: {details}")
    return False
