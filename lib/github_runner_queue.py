"""Shared host capacity, retained whenever runner cleanup is uncertain."""

from __future__ import annotations

import fcntl
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import time

from lib.build_coordinator import WAIT_SECONDS, clear_slot, drained, mark_slot, open_slot
from lib.github_runner_config import GATE, QUEUE_LOCK


def _runner_cgroup_idle() -> bool:
    """Confirm only this guardian and its immutable listener remain in the unit."""
    from lib.github_runner_config import APP
    import re

    raw = Path("/proc/self/cgroup").read_text()
    match = re.fullmatch(r"0::(/system.slice/basaltwater-github-runner(?:-[a-f0-9]{16})?\.service)\n", raw)
    if match is None:
        return False
    group = Path("/sys/fs/cgroup" + match[1])
    if list(group.glob("*/cgroup.procs")):
        return False
    pids = (group / "cgroup.procs").read_text().splitlines()
    if len(pids) > 4096:
        return False
    for value in pids:
        if not value.isascii() or not value.isdigit():
            return False
        if int(value) == os.getpid():
            continue
        try:
            executable = os.readlink("/proc/" + value + "/exe")
        except FileNotFoundError:
            continue
        if re.fullmatch(re.escape(APP) + r"(?:/instances/[a-f0-9]{16})?/bin/Runner.Listener", executable) is None:
            return False
    return True


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
        if _runner_cgroup_idle():
            clear_slot(lock)
            return 0
        return 1  # Retain the durable marker when descendants or probes are uncertain.
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
        if lock_path != QUEUE_LOCK:
            raise ValueError("Runner must use the shared build coordinator")
        lock = open_slot()
        deadline = time.monotonic() + WAIT_SECONDS
        print("GitHub runner queue: waiting for the host job slot", flush=True)
        while True:
            if drained() or time.monotonic() >= deadline:
                raise ValueError("Build host is drained or capacity wait expired")
            if select.select([worker], [], [], 0)[0]:
                raise ValueError("Runner worker exited while queued")
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                select.select([worker], [], [], 0.2)
        if drained() or os.fstat(lock).st_size:
            raise ValueError("Build host requires recovery or is drained")
        mark_slot(lock)
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
