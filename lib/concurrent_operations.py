"""Concurrent operation coordination for sync/scrub systems with memory-aware resource management."""

from __future__ import annotations

import hashlib
import os
import stat
import time
import threading

import fcntl
from typing import Any, Callable, Optional, TextIO
from dataclasses import dataclass, field
from enum import Enum
from collections import deque

from lib.operation_log import OperationLogger
from lib.types import BYTES_PER_MB, BYTES_PER_KB
from lib.validation import validate_filesystem_path


DEFAULT_OPERATION_LOCK_DIR = "/run/lock/basaltwater/operations"


class OperationType(Enum):
    """Types of operations that can be coordinated."""
    SYNC = "sync"
    SCRUB = "scrub"
    BACKUP = "backup"


class OperationPriority(Enum):
    """Priority levels for operations."""
    LOW = 1
    NORMAL = 2
    HIGH = 3
    CRITICAL = 4


@dataclass
class ResourceRequirement:
    """Resource requirements for an operation."""
    memory_mb: int
    cpu_percent: float
    io_weight: int = 1
    can_share_resources: bool = True


@dataclass
class Operation:
    """Represents an operation in the queue."""
    id: str
    type: OperationType
    priority: OperationPriority
    resource_req: ResourceRequirement
    paths: list[str]  # File paths involved
    callback: Callable[..., Any]
    logger: OperationLogger
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    
    @property
    def duration(self) -> Optional[float]:
        """Get operation duration if completed."""
        if self.started_at and self.completed_at:
            return self.completed_at - self.started_at
        return None


class MemoryMonitor:
    """Simple memory monitoring with configurable thresholds."""
    
    def __init__(self, warning_threshold_mb: int = 512, critical_threshold_mb: int = 256):
        self.warning_threshold = warning_threshold_mb * BYTES_PER_MB
        self.critical_threshold = critical_threshold_mb * BYTES_PER_MB
        self._lock = threading.Lock()
    
    def get_available_memory(self) -> int:
        """Get available memory in bytes from /proc/meminfo."""
        try:
            with open('/proc/meminfo', 'r') as f:
                for line in f:
                    if line.startswith('MemAvailable:'):
                        # Format: "MemAvailable:    1234567 kB"
                        kb_value = int(line.split()[1])
                        return kb_value * BYTES_PER_KB
        except (IOError, ValueError):
            # If /proc/meminfo is unreadable, treat as no available memory.
            pass
        return 0
    
    def get_memory_pressure_level(self) -> str:
        available = self.get_available_memory()
        if available < self.critical_threshold:
            return 'critical'
        elif available < self.warning_threshold:
            return 'warning'
        return 'normal'
    
    def can_allocate_memory(self, required_mb: int) -> bool:
        required_bytes = required_mb * BYTES_PER_MB
        available = self.get_available_memory()
        return (available - required_bytes) > self.critical_threshold


class SimpleLockManager:
    """File-based lock manager using flock. Properly tracks file handles to avoid leaks."""
    
    def __init__(self, lock_dir: str = DEFAULT_OPERATION_LOCK_DIR):
        validate_filesystem_path(lock_dir, must_exist=False)
        absolute_lock_dir = os.path.abspath(lock_dir)
        if os.path.realpath(absolute_lock_dir) != absolute_lock_dir:
            raise ValueError(f"Lock directory must not contain symlinks: {lock_dir}")

        self.lock_dir = lock_dir
        os.makedirs(lock_dir, mode=0o700, exist_ok=True)
        lock_dir_stat = os.lstat(lock_dir)
        if not stat.S_ISDIR(lock_dir_stat.st_mode):
            raise ValueError(f"Lock path is not a directory: {lock_dir}")
        if lock_dir_stat.st_uid != os.geteuid():
            raise PermissionError(f"Lock directory is not owned by the current user: {lock_dir}")
        if stat.S_IMODE(lock_dir_stat.st_mode) & 0o022:
            raise PermissionError(f"Lock directory is group- or world-writable: {lock_dir}")

        self._file_handles: dict[str, TextIO] = {}
    
    def _get_lock_path(self, resource: str) -> str:
        # Use hash to avoid overly long filenames
        safe_name = hashlib.sha256(resource.encode()).hexdigest()[:24]
        return os.path.join(self.lock_dir, f"{safe_name}.lock")

    def _open_lock_file(self, lock_path: str) -> TextIO:
        """Open a current-user-owned regular lock file without following links."""
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        file_descriptor = os.open(lock_path, flags, 0o600)
        try:
            lock_stat = os.fstat(file_descriptor)
            if not stat.S_ISREG(lock_stat.st_mode):
                raise ValueError(f"Lock path is not a regular file: {lock_path}")
            if lock_stat.st_uid != os.geteuid():
                raise PermissionError(f"Lock file is not owned by the current user: {lock_path}")
            if stat.S_IMODE(lock_stat.st_mode) & 0o022:
                raise PermissionError(f"Lock file is group- or world-writable: {lock_path}")
            os.fchmod(file_descriptor, 0o600)
            lock_file = os.fdopen(file_descriptor, "a+", encoding="utf-8")
            file_descriptor = -1
            return lock_file
        finally:
            if file_descriptor >= 0:
                os.close(file_descriptor)
    
    def acquire_lock(self, resource: str, exclusive: bool = True) -> bool:
        if resource in self._file_handles:
            return True  # Already locked by us
        
        lock_path = self._get_lock_path(resource)
        lock_mode = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        lock_file = None
        
        try:
            lock_file = self._open_lock_file(lock_path)
            fcntl.flock(lock_file.fileno(), lock_mode | fcntl.LOCK_NB)
            self._file_handles[resource] = lock_file
            return True
        except (IOError, OSError, ValueError):
            if lock_file:
                lock_file.close()
            return False
    
    def release_lock(self, resource: str) -> None:
        if resource not in self._file_handles:
            return
        
        try:
            lock_file = self._file_handles.pop(resource)
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            lock_file.close()
        except (IOError, OSError):
            # Best-effort unlock: ignore failures closing lock handles.
            pass
    
    def check_locked(self, resource: str, exclusive: bool = True) -> bool:
        if resource in self._file_handles:
            return True  # Locked by us
        
        lock_path = self._get_lock_path(resource)
        lock_mode = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        try:
            with self._open_lock_file(lock_path) as test_file:
                fcntl.flock(test_file.fileno(), lock_mode | fcntl.LOCK_NB)
                fcntl.flock(test_file.fileno(), fcntl.LOCK_UN)
            return False
        except (IOError, OSError, ValueError):
            return True


class OperationQueue:
    """Thread-safe operation queue with priority support."""
    
    def __init__(self, max_size: int = 50):
        self._queue: deque[Operation] = deque()
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._max_size = max_size
        self._operation_ids: set[str] = set()
        self._shutdown = False
    
    def enqueue(self, operation: Operation) -> bool:
        with self._condition:
            if self._shutdown:
                return False
            if operation.id in self._operation_ids:
                return False
            
            if len(self._queue) >= self._max_size:
                return False
            
            inserted = False
            for i, existing_op in enumerate(self._queue):
                if operation.priority.value > existing_op.priority.value:
                    self._queue.insert(i, operation)
                    inserted = True
                    break
            
            if not inserted:
                self._queue.append(operation)
            
            self._operation_ids.add(operation.id)
            self._condition.notify()
            return True
    
    def dequeue(self) -> Optional[Operation]:
        with self._condition:
            while not self._queue and not self._shutdown:
                self._condition.wait(timeout=1.0)
            
            if not self._queue:
                return None
            
            operation = self._queue.popleft()
            self._operation_ids.discard(operation.id)
            return operation

    def shutdown(self):
        with self._condition:
            self._shutdown = True
            self._condition.notify_all()
    
    def peek(self) -> Optional[Operation]:
        with self._lock:
            return self._queue[0] if self._queue else None
    
    def remove(self, operation_id: str) -> bool:
        with self._lock:
            for i, op in enumerate(self._queue):
                if op.id == operation_id:
                    del self._queue[i]
                    self._operation_ids.discard(operation_id)
                    return True
            return False
    
    def size(self) -> int:
        with self._lock:
            return len(self._queue)
    
    def get_queue_info(self) -> dict[str, Any]:
        with self._lock:
            priorities: dict[str, int] = {}
            for op in self._queue:
                p = op.priority.name
                priorities[p] = priorities.get(p, 0) + 1
            
            return {
                'size': len(self._queue),
                'max_size': self._max_size,
                'priorities': priorities,
                'oldest_age': time.time() - self._queue[0].created_at if self._queue else 0
            }

    @property
    def max_size(self) -> int:
        return self._max_size


class ConcurrentOperationManager:
    """Main coordinator for concurrent operations."""
    
    def __init__(self, max_concurrent: int = 3, memory_warning_mb: int = 512, 
                 memory_critical_mb: int = 256,
                 lock_dir: str = DEFAULT_OPERATION_LOCK_DIR):
        self.max_concurrent = max_concurrent
        self.memory_monitor = MemoryMonitor(memory_warning_mb, memory_critical_mb)
        self.lock_manager = SimpleLockManager(lock_dir)
        self.queue = OperationQueue()
        
        self._running_operations: dict[str, Operation] = {}
        self._operation_lock = threading.RLock()
        self._idle_condition = threading.Condition(self._operation_lock)
        self._pending_operations = 0
        self._shutdown = False
        self._shutdown_event = threading.Event()
        
        self._metrics = {
            'operations_started': 0,
            'operations_completed': 0,
            'operations_failed': 0,
            'memory_throttles': 0,
            'resource_conflicts': 0
        }

        self._workers: list[threading.Thread] = []
        self._start_workers()
    
    def _start_workers(self) -> None:
        for i in range(self.max_concurrent):
            worker = threading.Thread(target=self._worker_loop, daemon=True, name=f"OpWorker-{i}")
            worker.start()
            self._workers.append(worker)
    
    def _worker_loop(self) -> None:
        while not self._shutdown:
            try:
                execution_context = self._get_execution_context()
                if not execution_context:
                    continue

                if not self._can_run_operation(execution_context):
                    if not self.queue.enqueue(execution_context):
                        self._mark_operation_finished()
                    self._shutdown_event.wait(timeout=1.0)
                    continue
                
                with self._operation_lock:
                    self._running_operations[execution_context.id] = execution_context
                    execution_context.started_at = time.time()
                    self._metrics['operations_started'] += 1
                
                acquired_locks = self._acquire_operation_locks(execution_context)
                if not acquired_locks:
                    with self._operation_lock:
                        del self._running_operations[execution_context.id]
                        execution_context.started_at = None
                    if not self.queue.enqueue(execution_context):
                        self._mark_operation_finished()
                    self._shutdown_event.wait(timeout=2.0)
                    continue
                
                try:
                    execution_context.logger.log_step("execution", "started", 
                                                    f"Starting {execution_context.type.value} operation")
                    
                    execution_context.callback()
                    
                    execution_context.completed_at = time.time()
                    execution_context.logger.log_step("execution", "completed", 
                                                    f"Completed {execution_context.type.value} operation")
                    
                except Exception as e:
                    execution_context.completed_at = time.time()
                    execution_context.logger.log_error("execution_failed", str(e))
                    with self._operation_lock:
                        self._metrics['operations_failed'] += 1
                else:
                    with self._operation_lock:
                        self._metrics['operations_completed'] += 1
                
                finally:
                    for lock_path in acquired_locks:
                        self.lock_manager.release_lock(lock_path)
                    
                    with self._operation_lock:
                        self._running_operations.pop(execution_context.id, None)
                        self._pending_operations = max(0, self._pending_operations - 1)
                        self._idle_condition.notify_all()
                        
            except Exception as e:
                print(f"Worker thread error: {e}")
                self._shutdown_event.wait(timeout=1.0)
    
    def wait_until_idle(self, timeout: float = 0.0) -> bool:
        """Wait until all operations are completed and queue is empty."""
        start_time = time.time()
        with self._idle_condition:
            while self._pending_operations > 0:
                if timeout <= 0:
                    self._idle_condition.wait(timeout=0.5)
                    continue

                remaining = timeout - (time.time() - start_time)
                if remaining <= 0:
                    return False
                self._idle_condition.wait(timeout=min(0.5, remaining))
            return True

    def _mark_operation_finished(self) -> None:
        """Record a terminal operation when work could not be requeued."""
        with self._idle_condition:
            self._pending_operations = max(0, self._pending_operations - 1)
            self._idle_condition.notify_all()

    def _get_execution_context(self) -> Optional[Operation]:
        return self.queue.dequeue()
    
    def _can_run_operation(self, operation: Operation) -> bool:
        """Check if operation can be run based on current conditions.
        
        Args:
            operation: Operation to check
            
        Returns:
            bool: True if operation can run
        """
        # Check memory pressure
        memory_pressure = self.memory_monitor.get_memory_pressure_level()
        
        if memory_pressure == 'critical':
            operation.logger.log_warning("Critical memory pressure, throttling operations", 
                                        {"memory_pressure": memory_pressure})
            self._metrics['memory_throttles'] += 1
            return False
        
        if memory_pressure == 'warning':
            # Only allow high/critical priority operations during memory warning
            if operation.priority.value < OperationPriority.HIGH.value:
                operation.logger.log_warning(f"Memory pressure, skipping {operation.priority.name} priority", 
                                            {"memory_pressure": memory_pressure, "priority": operation.priority.name})
                self._metrics['memory_throttles'] += 1
                return False
        
        # Check if we can allocate required memory
        if not self.memory_monitor.can_allocate_memory(operation.resource_req.memory_mb):
            operation.logger.log_warning(f"Insufficient memory for {operation.resource_req.memory_mb}MB requirement", 
                                       {"required_mb": operation.resource_req.memory_mb, "available_mb": self.memory_monitor.get_available_memory() // (1024 * 1024)})
            self._metrics['memory_throttles'] += 1
            return False
        
        # Check resource conflicts
        for path in operation.paths:
            if self.lock_manager.check_locked(path):
                operation.logger.log_warning(f"Resource already in use: {path}", 
                                           {"path": path})
                self._metrics['resource_conflicts'] += 1
                return False
        
        return True
    
    def _acquire_operation_locks(self, operation: Operation) -> list[str]:
        """Acquire locks for operation paths.
        
        Args:
            operation: Operation requiring locks
            
        Returns:
            List of successfully acquired lock paths, empty if any failed
        """
        acquired_locks: list[str] = []
        
        for path in operation.paths:
            if self.lock_manager.acquire_lock(path):
                acquired_locks.append(path)
            else:
                # Release any acquired locks and return failure
                for lock_path in acquired_locks:
                    self.lock_manager.release_lock(lock_path)
                return []
        
        return acquired_locks
    
    def submit_operation(self, operation_id: str, operation_type: OperationType,
                        priority: OperationPriority, resource_req: ResourceRequirement,
                        paths: list[str], callback: Callable[..., Any],
                        logger: OperationLogger) -> bool:
        operation = Operation(
            id=operation_id,
            type=operation_type,
            priority=priority,
            resource_req=resource_req,
            paths=paths,
            callback=callback,
            logger=logger
        )
        
        with self._operation_lock:
            success = self.queue.enqueue(operation)
            if success:
                self._pending_operations += 1

        if success:
            logger.log_step("queued", "completed", f"Operation queued with {priority.name} priority")
        else:
            logger.log_warning("Operation queue is full", {"max_size": self.queue.max_size})
        
        return success
    
    def get_status(self) -> dict[str, Any]:
        with self._operation_lock:
            running_count = len(self._running_operations)
            running_ops = list(self._running_operations.values())
        
        total_memory_req = sum(op.resource_req.memory_mb for op in running_ops)
        total_cpu_req = sum(op.resource_req.cpu_percent for op in running_ops)
        
        return {
            'running_operations': running_count,
            'max_concurrent': self.max_concurrent,
            'queue_info': self.queue.get_queue_info(),
            'memory_pressure': self.memory_monitor.get_memory_pressure_level(),
            'available_memory_mb': self.memory_monitor.get_available_memory() // (1024 * 1024),
            'current_usage': {'memory_mb': total_memory_req, 'cpu_percent': total_cpu_req},
            'metrics': self._metrics.copy()
        }
    
    def cancel_operation(self, operation_id: str) -> bool:
        removed = self.queue.remove(operation_id)
        if removed:
            self._mark_operation_finished()
        return removed
    
    def shutdown(self) -> None:
        self._shutdown = True
        self._shutdown_event.set()
        self.queue.shutdown()
        for worker in self._workers:
            worker.join(timeout=5.0)
    
    def get_resource_conflicts(self, paths: list[str]) -> list[str]:
        conflicts: list[str] = []
        for path in paths:
            if self.lock_manager.check_locked(path):
                conflicts.append(path)
        return conflicts


# Global instance
_operation_manager: Optional[ConcurrentOperationManager] = None


def get_operation_manager(**kwargs: Any) -> ConcurrentOperationManager:
    global _operation_manager
    if _operation_manager is None:
        _operation_manager = ConcurrentOperationManager(**kwargs)
    return _operation_manager
