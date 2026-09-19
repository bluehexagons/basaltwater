"""Operation logging framework extending logging_utils.py for audit trails."""

from __future__ import annotations
import json
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from lib.logging_utils import get_rotating_logger, log_message


class OperationLogger:
    """Enhanced operation logger with state tracking and audit trails."""
    
    def __init__(self, operation_id: str, log_file: str):
        """Initialize operation logger.
        
        Args:
            operation_id: Unique identifier for the operation
            log_file: Path to log file
        """
        self.operation_id = operation_id
        self.log_file = log_file
        self.logger = get_rotating_logger(f"operation_{operation_id}", log_file)
        self.start_time = time.time()
        self.checkpoints: dict[str, dict[str, Any]] = {}
        self.current_step: Optional[str] = None
        self.status = "running"
        
        # Log operation start
        self._log_event("operation_start", {
            "operation_id": operation_id,
            "start_time": datetime.fromtimestamp(self.start_time).isoformat(),
            "log_file": log_file
        })
    
    def log_step(self, step: str, status: str, details: Optional[str] = None, duration: Optional[float] = None) -> None:
        """Log a step in the operation.
        
        Args:
            step: Step name/identifier
            status: Status ('started', 'completed', 'failed', 'warning')
            details: Optional details about the step
            duration: Optional duration in seconds
        """
        self.current_step = step
        
        event_data: dict[str, Any] = {
            "step": step,
            "status": status,
            "timestamp": datetime.now().isoformat()
        }
        
        if details:
            event_data["details"] = details
        
        if duration is not None:
            event_data["duration_seconds"] = round(duration, 2)
        
        self._log_event("step", event_data)
    
    def create_checkpoint(self, checkpoint_name: str, state: dict[str, Any]) -> None:
        """Create a checkpoint with operation state.
        
        Args:
            checkpoint_name: Name for the checkpoint
            state: Dictionary containing operation state
        """
        checkpoint_data: dict[str, Any] = {
            "checkpoint_name": checkpoint_name,
            "state": state,
            "timestamp": datetime.now().isoformat(),
            "elapsed_time_seconds": round(time.time() - self.start_time, 2)
        }
        
        self.checkpoints[checkpoint_name] = checkpoint_data
        self._log_event("checkpoint", checkpoint_data)
    
    def log_rollback(self, from_checkpoint: str, reason: str) -> None:
        """Log rollback operation.
        
        Args:
            from_checkpoint: Checkpoint to rollback from
            reason: Reason for rollback
        """
        rollback_data: dict[str, Any] = {
            "from_checkpoint": from_checkpoint,
            "reason": reason,
            "timestamp": datetime.now().isoformat(),
            "checkpoint_available": from_checkpoint in self.checkpoints
        }
        
        if from_checkpoint in self.checkpoints:
            rollback_data["checkpoint_state"] = self.checkpoints[from_checkpoint]["state"]
        
        self.status = "rolled_back"
        self._log_event("rollback", rollback_data)
    
    def log_error(self, error_type: str, error_message: str, context: Optional[dict[str, Any]] = None) -> None:
        """Log an error with context.
        
        Args:
            error_type: Type of error
            error_message: Error message
            context: Additional context information
        """
        error_data: dict[str, Any] = {
            "error_type": error_type,
            "error_message": error_message,
            "timestamp": datetime.now().isoformat(),
            "current_step": self.current_step
        }
        
        if context:
            error_data["context"] = context
        
        self._log_event("error", error_data)
    
    def log_warning(self, warning_message: str, context: Optional[dict[str, Any]] = None) -> None:
        """Log a warning with context.
        
        Args:
            warning_message: Warning message
            context: Additional context information
        """
        warning_data: dict[str, Any] = {
            "warning_message": warning_message,
            "timestamp": datetime.now().isoformat(),
            "current_step": self.current_step
        }
        
        if context:
            warning_data["context"] = context
        
        self._log_event("warning", warning_data)
    
    def log_metric(self, metric_name: str, value: Any, unit: Optional[str] = None) -> None:
        """Log a performance or operational metric.
        
        Args:
            metric_name: Name of the metric
            value: Metric value
            unit: Optional unit (e.g., 'MB', 'seconds', 'files')
        """
        metric_data: dict[str, Any] = {
            "metric_name": metric_name,
            "value": value,
            "timestamp": datetime.now().isoformat()
        }
        
        if unit:
            metric_data["unit"] = unit
        
        self._log_event("metric", metric_data)
    
    def complete(self, status: str = "completed", summary: Optional[str] = None) -> None:
        """Complete the operation with final status.
        
        Args:
            status: Final status ('completed', 'failed', 'cancelled')
            summary: Optional summary message
        """
        self.status = status
        end_time = time.time()
        duration = end_time - self.start_time
        
        completion_data: dict[str, Any] = {
            "status": status,
            "end_time": datetime.fromtimestamp(end_time).isoformat(),
            "duration_seconds": round(duration, 2),
            "checkpoints_created": len(self.checkpoints)
        }
        
        if summary:
            completion_data["summary"] = summary
        
        self._log_event("operation_complete", completion_data)
    
    def get_checkpoint(self, checkpoint_name: str) -> Optional[dict[str, Any]]:
        """Retrieve checkpoint state.
        
        Args:
            checkpoint_name: Name of checkpoint to retrieve
            
        Returns:
            Checkpoint data if found, None otherwise
        """
        return self.checkpoints.get(checkpoint_name)
    
    def get_all_checkpoints(self) -> dict[str, dict[str, Any]]:
        """Get all checkpoints.
        
        Returns:
            Dictionary of all checkpoints
        """
        return self.checkpoints.copy()
    
    def get_operation_summary(self) -> dict[str, Any]:
        """Get operation summary.
        
        Returns:
            Dictionary with operation summary
        """
        return {
            "operation_id": self.operation_id,
            "status": self.status,
            "start_time": datetime.fromtimestamp(self.start_time).isoformat(),
            "elapsed_time_seconds": round(time.time() - self.start_time, 2),
            "current_step": self.current_step,
            "checkpoints_count": len(self.checkpoints),
            "log_file": self.log_file
        }
    
    def _log_event(self, event_type: str, data: dict[str, Any]) -> None:
        """Log an event to the log file.
        
        Args:
            event_type: Type of event
            data: Event data
        """
        log_entry: dict[str, Any] = {
            "event_type": event_type,
            "operation_id": self.operation_id,
            **data
        }
        
        # Use the existing log_message function for consistent logging
        log_message(self.logger, json.dumps(log_entry, default=str))

    def log_context(self, event_type: str, data: dict[str, Any]) -> None:
        """Public wrapper for logging contextual events (safe for external callers)."""
        self._log_event(event_type, data)


class OperationLoggerManager:
    """Manager for multiple operation loggers."""
    
    def __init__(self, base_log_dir: str):
        """Initialize logger manager.
        
        Args:
            base_log_dir: Base directory for operation logs
        """
        self.base_log_dir = Path(base_log_dir)
        self.base_log_dir.mkdir(parents=True, exist_ok=True)
        self.active_loggers: dict[str, OperationLogger] = {}
    
    def create_logger(self, operation_type: str, **kwargs: Any) -> OperationLogger:
        """Create a new operation logger.
        
        Args:
            operation_type: Type of operation (e.g., 'sync', 'scrub', 'par2')
            **kwargs: Additional context for operation
            
        Returns:
            New OperationLogger instance
        """
        operation_id = str(uuid.uuid4())[:8]
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = self.base_log_dir / f"{operation_type}_{timestamp}_{operation_id}.log"
        
        logger = OperationLogger(operation_id, str(log_file))
        
        # Log creation context using public wrapper
        logger.log_context("logger_created", {
            "operation_type": operation_type,
            "context": kwargs
        })
        
        self.active_loggers: dict[str, OperationLogger] = self.active_loggers if hasattr(self, 'active_loggers') else {}
        self.active_loggers[operation_id] = logger
        return logger
    
    def get_logger(self, operation_id: str) -> Optional[OperationLogger]:
        """Get active logger by operation ID.
        
        Args:
            operation_id: Operation ID to retrieve
            
        Returns:
            OperationLogger if found, None otherwise
        """
        return self.active_loggers.get(operation_id)
    
    def complete_logger(self, operation_id: str) -> None:
        """Mark logger as complete and remove from active loggers.
        
        Args:
            operation_id: Operation ID to complete
        """
        if operation_id in self.active_loggers:
            logger = self.active_loggers[operation_id]
            if logger.status == "running":
                logger.complete("completed")
            del self.active_loggers[operation_id]
    
    def get_active_operations(self) -> list[str]:
        """Get list of active operation IDs.
        
        Returns:
            List of active operation IDs
        """
        return list(self.active_loggers.keys())
    
    def cleanup_old_logs(self, days_to_keep: int = 30) -> int:
        """Clean up old operation logs.
        
        Args:
            days_to_keep: Number of days to keep logs
            
        Returns:
            Number of files cleaned up
        """
        cutoff_time = time.time() - (days_to_keep * 24 * 60 * 60)
        cleaned_count = 0
        
        for log_file in self.base_log_dir.glob("*.log"):
            if log_file.stat().st_mtime < cutoff_time:
                try:
                    log_file.unlink()
                    cleaned_count += 1
                except OSError:
                    # Best-effort cleanup: ignore errors removing old log file.
                    pass
        
        return cleaned_count


# Global logger manager instance
_logger_manager = None


def get_operation_logger_manager(base_log_dir: str = "/var/log/basaltwater/operations") -> OperationLoggerManager:
    """Get or create the global operation logger manager.
    
    Args:
        base_log_dir: Base directory for operation logs
        
    Returns:
        OperationLoggerManager instance
    """
    global _logger_manager
    if _logger_manager is None:
        _logger_manager = OperationLoggerManager(base_log_dir)
    return _logger_manager


def set_operation_logger_manager(manager: Optional[OperationLoggerManager]) -> None:
    """Set the global operation logger manager (for tests and programmatic control)."""
    global _logger_manager
    _logger_manager = manager


def create_operation_logger(operation_type: str, **kwargs: Any) -> OperationLogger:
    """Convenience function to create a new operation logger.
    
    Args:
        operation_type: Type of operation
        **kwargs: Additional context
        
    Returns:
        New OperationLogger instance
    """
    manager = get_operation_logger_manager()
    return manager.create_logger(operation_type, **kwargs)
