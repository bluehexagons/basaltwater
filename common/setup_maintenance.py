"""Run configured maintenance once at the end of an explicit setup run."""

from __future__ import annotations

import os
import shlex
from typing import Final

from lib.config import SetupConfig
from lib.remote_utils import get_user_home, is_dry_run, run
from lib.validation import validate_filesystem_path

from .common_steps import _run_as_login_user


_PYTHON: Final[str] = "/usr/bin/python3"
_SYSTEM_CLEANUP_SCRIPT: Final[str] = (
    "/opt/infra_tools/common/service_tools/cleanup_maintenance.py"
)
_USER_CACHE_SCRIPT: Final[str] = (
    "/opt/infra_tools/common/service_tools/user_cache_maintenance.py"
)
_MAINTENANCE_TIMEOUT_SECONDS: Final[int] = 60 * 60
_MAX_FAILURE_DETAIL: Final[int] = 2000


def _validated_script(path: str, label: str) -> str:
    """Return a trusted maintenance script path from the deployed checkout."""
    validate_filesystem_path(path, must_exist=True)
    if os.path.islink(path) or not os.path.isfile(path):
        raise RuntimeError(f"{label} is not a regular file: {path}")
    return path


def _failure_detail(result: object) -> str:
    """Return bounded command output suitable for setup diagnostics."""
    detail = (
        getattr(result, "stderr", None)
        or getattr(result, "stdout", None)
        or f"exited with code {getattr(result, 'returncode', 'unknown')}"
    )
    detail = str(detail).strip()
    if len(detail) <= _MAX_FAILURE_DETAIL:
        return detail
    head_length = _MAX_FAILURE_DETAIL // 2
    tail_length = _MAX_FAILURE_DETAIL - head_length
    return (
        detail[:head_length].rstrip()
        + "\n... output omitted ...\n"
        + detail[-tail_length:].lstrip()
    )


def _run_system_cleanup() -> bool:
    """Run root-owned cleanup and return whether it completed successfully."""
    script = _validated_script(_SYSTEM_CLEANUP_SCRIPT, "System cleanup script")
    print("  Running system cleanup now (this may take a while)...")
    result = run(
        [_PYTHON, script],
        check=False,
        capture_output=True,
        timeout=_MAINTENANCE_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        print(
            "  ⚠ System cleanup failed; the scheduled job will retry: "
            f"{_failure_detail(result)}"
        )
        return False
    print("  ✓ System cleanup completed")
    return True


def _run_user_cache_cleanup(config: SetupConfig) -> bool:
    """Run user-scoped cache cleanup and return whether it completed successfully."""
    if config.username == "root":
        return True

    script = _validated_script(_USER_CACHE_SCRIPT, "User cache maintenance script")
    user_home = get_user_home(config.username)
    command = shlex.join([_PYTHON, script])
    print("  Running user cache cleanup now (this may take a while)...")
    result = _run_as_login_user(
        config.username,
        user_home,
        command,
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        print(
            "  ⚠ User cache cleanup failed; the scheduled job will retry: "
            f"{_failure_detail(result)}"
        )
        return False
    print("  ✓ User cache cleanup completed")
    return True


def run_setup_maintenance(config: SetupConfig) -> None:
    """Run the same bounded cleanup jobs used by recurring maintenance timers.

    Setup runs this after the selected installation and reconciliation steps so
    an infrequently started host receives a maintenance opportunity immediately.
    Maintenance failures remain warnings because the persistent timer can retry
    them without preventing unrelated setup configuration from completing.
    """
    if is_dry_run():
        print("  [DRY-RUN] Would run system and user cache maintenance")
        return

    _run_system_cleanup()
    _run_user_cache_cleanup(config)
