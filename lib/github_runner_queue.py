"""One host-wide job slot, released even when a Runner.Worker crashes."""

from __future__ import annotations

import fcntl
import os
from pathlib import Path
import select
import signal
import stat
import subprocess
import sys

from lib.github_runner_config import GATE, QUEUE_LOCK


def hold_job_slot(lock: int, worker: int, ready: int) -> int:
    """Keep the inherited flock until the pidfd reports worker termination."""
    def interrupted(_signal, _frame):
        raise InterruptedError("Queue guardian interrupted")

    for name in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(name, interrupted)
    try:
        os.write(ready, b"ready")
        os.close(ready)
        select.select([worker], [], [])
        return 0
    finally:
        # Never release the slot on an unexpected guardian failure while its
        # worker could still run workflow steps.
        if not select.select([worker], [], [], 0)[0]:
            signal.pidfd_send_signal(worker, signal.SIGKILL)
        os.close(worker)
        os.close(lock)


def acquire_job_slot(worker_pid: int, *, lock_path: str = QUEUE_LOCK, entry: str = GATE) -> None:
    """Wait before steps, then hand the lock to a clean-environment guardian.

    The guardian must not inherit RUNNER_TRACKING_ID: GitHub's normal orphan
    cleanup would otherwise kill it during finalization before the worker exits.
    """
    if type(worker_pid) is not int or worker_pid <= 1:
        raise ValueError("Invalid runner worker")
    # The generated hook passes its own parent, never a workflow environment PID.
    parent = Path(f"/proc/{os.getppid()}/stat").read_text().rsplit(") ", 1)[1].split()[1]
    if int(parent) != worker_pid:
        raise ValueError("Queue worker does not own the hook")
    worker = os.pidfd_open(worker_pid)
    lock = None
    try:
        lock = os.open(lock_path, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
        info = os.fstat(lock)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600):
            raise ValueError("Unsafe runner queue lock")
        print("GitHub runner queue: waiting for the host job slot", flush=True)
        while True:
            if select.select([worker], [], [], 0)[0]:
                raise ValueError("Runner worker exited while queued")
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                select.select([worker], [], [], 0.2)
        ready, outgoing = os.pipe()
        try:
            guardian = subprocess.Popen(
                [sys.executable, "-I", entry, "--hold-queue", str(lock), str(worker), str(outgoing)],
                env={"PATH": "/usr/bin:/bin"}, pass_fds=(lock, worker, outgoing),
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            os.close(outgoing)
            outgoing = -1
            if not select.select([ready], [], [], 10)[0] or os.read(ready, 6) != b"ready":
                guardian.terminate()
                guardian.wait(timeout=10)
                raise ValueError("Runner queue guardian unavailable")
        finally:
            os.close(ready)
            if outgoing >= 0:
                os.close(outgoing)
        print("GitHub runner queue: host job slot acquired", flush=True)
    finally:
        os.close(worker)
        if lock is not None:
            os.close(lock)
