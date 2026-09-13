"""One command budget shared by all phases of a CI/CD job."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import subprocess
import time

from lib.remote_utils import CommandTimeoutError, run


JOB_TIMEOUT_SECONDS = 4 * 60 * 60


@dataclass
class JobBudget:
    deadline: float
    phase: str = "checkout"
    deployment_started: bool = False
    timed_out: bool = False


_budget: ContextVar[JobBudget | None] = ContextVar("cicd_job_budget", default=None)


@contextmanager
def job_budget():
    budget = JobBudget(time.monotonic() + JOB_TIMEOUT_SECONDS)
    token = _budget.set(budget)
    try:
        yield budget
    finally:
        _budget.reset(token)


def command_timeout(limit: float) -> float:
    budget = _budget.get()
    if budget is None:
        return limit
    remaining = budget.deadline - time.monotonic()
    if remaining <= 0:
        budget.timed_out = True
        raise subprocess.TimeoutExpired("CI/CD job", JOB_TIMEOUT_SECONDS)
    return min(limit, remaining)


def enter_phase(phase: str, *, deployment: bool = False) -> None:
    command_timeout(JOB_TIMEOUT_SECONDS)
    budget = _budget.get()
    if budget is not None:
        budget.phase = phase
        budget.deployment_started |= deployment


def run_command(command, *, timeout: float, input=None, **kwargs):
    """Use shared descendant cleanup and the remaining job budget."""
    timeout = command_timeout(timeout)
    try:
        return run(command, check=False, input_data=input, timeout=timeout, **kwargs)
    except CommandTimeoutError as exc:
        budget = _budget.get()
        if budget is not None:
            budget.timed_out = True
        raise subprocess.TimeoutExpired(exc.command, timeout) from exc
