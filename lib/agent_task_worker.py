"""Foreground task scheduler entrypoint for a supervised account-owned service."""

from __future__ import annotations

import signal
import sys
import threading

from lib.agent_tasks import AgentTasks


def run_worker(*, once: bool = False, manager: AgentTasks | None = None) -> int:
    manager = manager or AgentTasks()
    if not manager.available():
        print("Task workers require a non-root account with its own home", file=sys.stderr)
        return 1
    stopped = threading.Event()
    previous = {}

    def stop(_number: int, _frame: object) -> None:
        stopped.set()
        manager._cancel.set()

    try:
        for number in (signal.SIGTERM, signal.SIGINT):
            previous[number] = signal.signal(number, stop)
        manager.start(once=once)
        if manager.error:
            print(manager.error, file=sys.stderr)
            return 1
        if once:
            while not stopped.is_set() and manager.tick():
                pass
        else:
            while not stopped.wait(1):
                if manager.error or manager._thread is None or not manager._thread.is_alive():
                    print(manager.error or "Task scheduler stopped unexpectedly", file=sys.stderr)
                    return 1
        return 0
    finally:
        manager.close()
        for number, handler in previous.items():
            signal.signal(number, handler)
