#!/usr/bin/env python3
"""Prune bounded developer-tool caches as the configured login user."""

from __future__ import annotations

import argparse
import json
import os
import pwd
import re
import shlex
import shutil
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from logging import INFO, WARNING

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "../.."))

from lib.agent_storage import (
    cleanup_codex_standalone_releases as reconcile_codex_standalone_releases,
    cleanup_t3_rotated_logs as reconcile_t3_rotated_logs,
)
from lib.logging_utils import get_service_logger, log_event
from lib.maintenance_defaults import (
    CLEANUP_COMMAND_TIMEOUT_SECONDS,
    CODEX_CACHE_MAX_BYTES,
    GO_BUILD_CACHE_MAX_BYTES,
    GO_MODULE_CACHE_MAX_BYTES,
    NPM_CACHE_MAX_BYTES,
    NPM_NPX_CACHE_MAX_BYTES,
    OPENCODE_CACHE_MAX_BYTES,
    PIP_CACHE_MAX_BYTES,
    STALE_NPX_CACHE_MAX_AGE_DAYS,
    STALE_USER_TOOL_CACHE_MAX_AGE_DAYS,
    STALE_USER_TOOL_TMP_MAX_AGE_DAYS,
    T3_ROTATED_LOG_MAX_AGE_DAYS,
    T3_ROTATED_LOG_MAX_BYTES,
    USER_CACHE_FREE_MAX_BYTES,
    USER_CACHE_FREE_MIN_BYTES,
    USER_CACHE_PRESSURE_MIN_BYTES,
)
from lib.types import BYTES_PER_MB, JSONDict
from lib.validation import validate_filesystem_path


logger = get_service_logger("user_cache_maintenance", "common", use_syslog=True)
_TOOL_NOT_FOUND_EXIT = 77
_T3_VERSION_PATTERN = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")


@dataclass(frozen=True)
class UserContext:
    """Identity and home directory for the unprivileged maintenance process."""

    username: str
    home: str
    uid: int


@dataclass(frozen=True)
class CacheUsage:
    """Size and most recent modification time for a cache tree."""

    size_bytes: int
    newest_mtime: float | None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse direct maintenance-script options."""
    parser = argparse.ArgumentParser(
        description="Audit and prune bounded caches owned by the current login user.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Inventory caches and report cleanup actions without changing files",
    )
    parser.add_argument(
        "--agent-storage-only",
        action="store_true",
        help="Reconcile only standalone agent releases and bounded log rotations",
    )
    return parser.parse_args(argv)


def resolve_user_context() -> UserContext:
    """Return the effective account and its validated home directory."""
    account = pwd.getpwuid(os.getuid())
    validate_filesystem_path(account.pw_dir, must_exist=True)
    return UserContext(account.pw_name, account.pw_dir, account.pw_uid)


def _tool_environment(context: UserContext) -> dict[str, str]:
    """Build an explicit home-scoped environment for optional tool commands."""
    environment = os.environ.copy()
    path_entries = (
        os.path.join(context.home, ".local", "bin"),
        os.path.join(context.home, ".opencode", "bin"),
        "/usr/local/go/bin",
        "/usr/local/sbin",
        "/usr/local/bin",
        "/usr/sbin",
        "/usr/bin",
        "/sbin",
        "/bin",
    )
    environment.update(
        {
            "HOME": context.home,
            "USER": context.username,
            "LOGNAME": context.username,
            "PATH": os.pathsep.join(path_entries),
            "PWD": context.home,
            "TMPDIR": "/tmp",
            "XDG_CACHE_HOME": os.path.join(context.home, ".cache"),
            "XDG_CONFIG_HOME": os.path.join(context.home, ".config"),
            "XDG_DATA_HOME": os.path.join(context.home, ".local", "share"),
            "XDG_STATE_HOME": os.path.join(context.home, ".local", "state"),
            "CODEX_HOME": os.path.join(context.home, ".codex"),
            "NVM_DIR": os.path.join(context.home, ".nvm"),
        }
    )
    return environment


def run_tool_command(
    context: UserContext,
    command: list[str],
    *,
    load_nvm: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run an allowlisted tool command in the configured user's environment."""
    if not command:
        raise ValueError("Tool command must not be empty")

    path_prefix = os.pathsep.join(
        (
            os.path.join(context.home, ".local", "bin"),
            os.path.join(context.home, ".opencode", "bin"),
            "/usr/local/go/bin",
        )
    )
    shell_parts = [f"export PATH={shlex.quote(path_prefix)}:$PATH"]
    if load_nvm:
        nvm_dir = os.path.join(context.home, ".nvm")
        shell_parts.extend(
            (
                f"export NVM_DIR={shlex.quote(nvm_dir)}",
                '[ ! -s "$NVM_DIR/nvm.sh" ] || . "$NVM_DIR/nvm.sh"',
            )
        )
    shell_parts.extend(
        (
            f"command -v {shlex.quote(command[0])} >/dev/null || "
            f"exit {_TOOL_NOT_FOUND_EXIT}",
            f"exec {shlex.join(command)}",
        )
    )
    return subprocess.run(
        # Do not use a login shell here. Unattended maintenance must not depend
        # on, execute, or have its output polluted by interactive profile files.
        ["/bin/bash", "-c", "; ".join(shell_parts)],
        capture_output=True,
        text=True,
        cwd=context.home,
        env=_tool_environment(context),
        timeout=CLEANUP_COMMAND_TIMEOUT_SECONDS,
    )


def run_cleanup_command(
    context: UserContext,
    command: list[str],
    action: str,
    *,
    dry_run: bool,
    load_nvm: bool = False,
    cache_path: str | None = None,
) -> str | None:
    """Run a supported cache command and return a concise failure summary."""
    if dry_run:
        log_event(
            logger,
            "Would run user cache cleanup command",
            action=action,
            command=shlex.join(command),
        )
        return None

    try:
        before = cache_usage(cache_path) if cache_path else None
        result = run_tool_command(context, command, load_nvm=load_nvm)
    except subprocess.TimeoutExpired:
        details = f"timed out after {CLEANUP_COMMAND_TIMEOUT_SECONDS}s"
        log_event(logger, f"{action} timed out", level=WARNING, error=details)
        return f"{action}: {details}"
    except OSError as exc:
        details = str(exc)
        log_event(logger, f"{action} could not run", level=WARNING, error=details)
        return f"{action}: {details}"

    if result.returncode == _TOOL_NOT_FOUND_EXIT:
        log_event(logger, f"{action} not available, skipping")
        return None
    if result.returncode != 0:
        details = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        log_event(logger, f"{action} failed", level=WARNING, error=details)
        return f"{action}: {details}"

    log_event(logger, f"{action} completed", level=INFO)
    if before is not None and cache_path is not None:
        try:
            after = cache_usage(cache_path)
            log_event(
                logger, "User cache cleanup result", action=action,
                removed_mb=round(max(0, before.size_bytes - after.size_bytes) / BYTES_PER_MB, 1),
                retained_mb=round(after.size_bytes / BYTES_PER_MB, 1),
            )
        except OSError as exc:
            return f"{action} result inventory: {exc}"
    return None


def query_cache_path(
    context: UserContext,
    commands: tuple[list[str], ...],
    action: str,
    *,
    load_nvm: bool = False,
) -> tuple[str | None, str | None, str | None]:
    """Return a tool-reported cache path, selected executable, and failure."""
    for command in commands:
        try:
            result = run_tool_command(context, command, load_nvm=load_nvm)
        except (OSError, subprocess.SubprocessError) as exc:
            details = str(exc)
            log_event(logger, f"{action} could not run", level=WARNING, error=details)
            return None, None, f"{action}: {details}"

        if result.returncode == _TOOL_NOT_FOUND_EXIT:
            continue
        if result.returncode != 0:
            details = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
            log_event(logger, f"{action} failed", level=WARNING, error=details)
            return None, None, f"{action}: {details}"

        output_lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        if not output_lines:
            details = "tool returned an empty cache path"
            log_event(logger, f"{action} failed", level=WARNING, error=details)
            return None, None, f"{action}: {details}"
        return output_lines[-1], command[0], None

    log_event(logger, f"{action} not available, skipping")
    return None, None, None


def is_safe_managed_path(context: UserContext, path: str, label: str) -> bool:
    """Return whether a cache path is absolute, home-contained, and not a link."""
    try:
        validate_filesystem_path(path, must_exist=False)
        if not os.path.isabs(path):
            raise ValueError("path is not absolute")
        absolute_home = os.path.abspath(context.home)
        absolute_path = os.path.abspath(path)
        if absolute_path == absolute_home:
            raise ValueError("path is the user home")
        if os.path.commonpath((absolute_home, absolute_path)) != absolute_home:
            raise ValueError("path is outside the user home")
        resolved_home = os.path.realpath(context.home)
        resolved_path = os.path.realpath(path)
        if resolved_path == resolved_home:
            raise ValueError("path resolves to the user home")
        if os.path.commonpath((resolved_home, resolved_path)) != resolved_home:
            raise ValueError("path resolves outside the user home")
        current_path = absolute_home
        for component in os.path.relpath(absolute_path, absolute_home).split(os.path.sep):
            current_path = os.path.join(current_path, component)
            if os.path.lexists(current_path) and os.path.islink(current_path):
                raise ValueError(f"path contains a symbolic link: {current_path}")
    except (OSError, ValueError) as exc:
        log_event(
            logger,
            "Skipping unsafe user cache path",
            level=WARNING,
            cache=label,
            path=path,
            error=str(exc),
        )
        return False
    return True


def cache_usage(path: str) -> CacheUsage:
    """Measure a cache tree without following symbolic links."""
    if not os.path.exists(path):
        return CacheUsage(0, None)

    size_bytes = 0
    newest_mtime: float | None = None
    pending_paths = [path]
    while pending_paths:
        current_path = pending_paths.pop()
        stat_result = os.lstat(current_path)
        if os.path.islink(current_path):
            continue
        if newest_mtime is None or stat_result.st_mtime > newest_mtime:
            newest_mtime = stat_result.st_mtime
        if not os.path.isdir(current_path):
            size_bytes += stat_result.st_size
            continue

        with os.scandir(current_path) as entries:
            pending_paths.extend(entry.path for entry in entries)

    return CacheUsage(size_bytes, newest_mtime)


def inventory_cache(
    context: UserContext,
    label: str,
    path: str,
) -> tuple[CacheUsage | None, str | None]:
    """Validate, measure, and log one user-owned cache."""
    if not is_safe_managed_path(context, path, label):
        return None, None
    try:
        usage = cache_usage(path)
    except OSError as exc:
        details = str(exc)
        log_event(
            logger,
            "Could not inspect user cache",
            level=WARNING,
            cache=label,
            path=path,
            error=details,
        )
        return None, f"{label} inventory: {details}"

    age_days = None
    if usage.newest_mtime is not None:
        age_days = max(0, int((time.time() - usage.newest_mtime) / (24 * 60 * 60)))
    log_event(
        logger,
        "User cache inventory",
        cache=label,
        path=path,
        size_mb=round(usage.size_bytes / BYTES_PER_MB, 1),
        newest_age_days=age_days,
    )
    return usage, None


def tool_is_active(process_names: tuple[str, ...], proc_root: str = "/proc") -> bool:
    """Detect matching current-user executables without reading command lines."""
    current_uid = os.getuid()
    try:
        process_entries = os.scandir(proc_root)
    except OSError:
        return True

    with process_entries:
        for entry in process_entries:
            if not entry.name.isdigit():
                continue
            try:
                if entry.stat(follow_symlinks=False).st_uid != current_uid:
                    continue
                with open(os.path.join(entry.path, "comm"), encoding="utf-8") as handle:
                    process_name = handle.read().strip().lower()
            except FileNotFoundError:
                continue
            except OSError:
                return True
            if any(
                process_name == name
                or process_name.startswith(f"{name}-")
                or process_name.startswith(f"{name} ")
                for name in process_names
            ):
                return True
    return False


def process_uses_path(path: str, proc_root: str = "/proc") -> bool:
    """Check accessible cwd/executable links and current-user argument vectors."""
    current_uid = os.getuid()
    absolute_path = os.path.abspath(path)
    encoded_path = os.fsencode(absolute_path)
    try:
        process_entries = os.scandir(proc_root)
    except OSError:
        return True

    with process_entries:
        for entry in process_entries:
            if not entry.name.isdigit():
                continue
            try:
                if entry.stat(follow_symlinks=False).st_uid != current_uid:
                    continue
            except (FileNotFoundError, ProcessLookupError):
                continue
            except OSError:
                return True
            # Non-dumpable user daemons (systemd, ssh-agent, etc.) commonly
            # hide these links. Their readable argv must still be checked;
            # one hidden cwd must not mark every user cache as occupied.
            for link in ("cwd", "exe"):
                try:
                    target = os.readlink(os.path.join(entry.path, link))
                    if os.path.commonpath((os.path.abspath(target), absolute_path)) == absolute_path:
                        return True
                except (OSError, ValueError):
                    pass
            try:
                with open(os.path.join(entry.path, "cmdline"), "rb") as handle:
                    arguments = handle.read()
                if any(
                    argument == encoded_path or argument.startswith(encoded_path + os.sep.encode())
                    for argument in arguments.split(b"\0")
                ):
                    return True
            except (FileNotFoundError, ProcessLookupError):
                continue
            except OSError:
                return True
    return False


def storage_pressure(path: str) -> bool:
    """Check available space on the cache's filesystem, including reserved blocks."""
    try:
        usage = shutil.disk_usage(path)
    except OSError as exc:
        log_event(logger, "Cannot assess cache filesystem", level=WARNING, error=str(exc))
        return False
    target = min(USER_CACHE_FREE_MAX_BYTES, max(USER_CACHE_FREE_MIN_BYTES, usage.total // 5))
    if usage.free >= target:
        return False
    log_event(
        logger, "Cache filesystem needs headroom",
        available_mb=round(usage.free / BYTES_PER_MB, 1),
        target_mb=round(target / BYTES_PER_MB, 1),
    )
    return True


def cache_needs_eviction(path: str, usage: CacheUsage, max_bytes: int) -> bool:
    """Recheck space before each eviction so later caches can be retained."""
    return usage.size_bytes > max_bytes or (
        usage.size_bytes >= USER_CACHE_PRESSURE_MIN_BYTES and storage_pressure(path)
    )


def defer_active_cache(path: str, process_names: tuple[str, ...]) -> bool:
    """Preserve caches referenced by a running tool or process path."""
    if tool_is_active(process_names) or process_uses_path(path):
        log_event(logger, "User cache cleanup deferred while in use", path=path)
        return True
    return False


def cleanup_managed_directory(
    context: UserContext,
    *,
    label: str,
    path: str,
    max_bytes: int,
    max_age_days: int,
    process_names: tuple[str, ...],
    dry_run: bool,
) -> list[str]:
    """Remove a rebuildable cache when stale or oversized and its tool is idle."""
    usage, failure = inventory_cache(context, label, path)
    if failure:
        return [failure]
    if usage is None or usage.newest_mtime is None:
        return []

    reasons: list[str] = []
    if usage.size_bytes > max_bytes:
        reasons.append("size limit exceeded")
    elif cache_needs_eviction(path, usage, max_bytes):
        reasons.append("low free space")
    cutoff = time.time() - (max_age_days * 24 * 60 * 60)
    if usage.newest_mtime < cutoff:
        reasons.append("stale")
    if not reasons:
        return []
    if defer_active_cache(path, process_names):
        log_event(
            logger,
            "User cache cleanup deferred while tool is active",
            cache=label,
            path=path,
            reason=", ".join(reasons),
        )
        return []

    if dry_run:
        log_event(
            logger,
            "Would remove rebuildable user cache",
            cache=label,
            path=path,
            reason=", ".join(reasons),
        )
        return []

    try:
        if not os.path.isdir(path):
            raise OSError("managed cache path is not a directory")
        shutil.rmtree(path)
    except OSError as exc:
        details = str(exc)
        log_event(
            logger,
            "Failed to remove rebuildable user cache",
            level=WARNING,
            cache=label,
            path=path,
            error=details,
        )
        return [f"{label} cleanup: {details}"]

    log_event(
        logger,
        "Removed rebuildable user cache",
        cache=label,
        path=path,
        reason=", ".join(reasons),
        removed_mb=round(usage.size_bytes / BYTES_PER_MB, 1),
    )
    return []


def cleanup_stale_children(
    context: UserContext,
    *,
    label: str,
    path: str,
    max_age_days: int,
    process_names: tuple[str, ...],
    dry_run: bool,
) -> list[str]:
    """Remove old non-link entries from an allowlisted tool temp directory."""
    if not is_safe_managed_path(context, path, label) or not os.path.exists(path):
        return []
    if tool_is_active(process_names):
        log_event(
            logger,
            "User temp cleanup deferred while tool is active",
            cache=label,
            path=path,
        )
        return []

    cutoff = time.time() - (max_age_days * 24 * 60 * 60)
    failures: list[str] = []
    removed_count = 0
    try:
        entries = list(os.scandir(path))
    except OSError as exc:
        return [f"{label} inventory: {exc}"]

    for entry in entries:
        if entry.is_symlink():
            continue
        try:
            usage = cache_usage(entry.path)
            if usage.newest_mtime is None or usage.newest_mtime >= cutoff:
                continue
            if dry_run:
                log_event(
                    logger,
                    "Would remove stale user tool temp entry",
                    cache=label,
                    path=entry.path,
                )
                continue
            if entry.is_dir(follow_symlinks=False):
                shutil.rmtree(entry.path)
            else:
                os.unlink(entry.path)
            removed_count += 1
        except OSError as exc:
            details = str(exc)
            log_event(
                logger,
                "Failed to remove stale user tool temp entry",
                level=WARNING,
                cache=label,
                path=entry.path,
                error=details,
            )
            failures.append(f"{entry.path}: {details}")

    if removed_count:
        log_event(
            logger,
            "Removed stale user tool temp entries",
            cache=label,
            path=path,
            removed_count=removed_count,
        )
    return failures


def cleanup_npm_cache(context: UserContext, *, dry_run: bool) -> list[str]:
    """Garbage-collect npm cache data and enforce a high-water size limit."""
    path, executable, failure = query_cache_path(
        context,
        (["npm", "config", "get", "cache"],),
        "npm cache path query",
        load_nvm=True,
    )
    if failure:
        return [failure]
    if path is None or executable is None:
        return []
    if defer_active_cache(path, ("npm", "npx", "pnpm", "yarn")):
        return []

    npx_path = os.path.join(path, "_npx")
    if process_uses_path(npx_path):
        log_event(
            logger,
            "npm npx workspace cleanup deferred while path is in use",
            path=npx_path,
        )
        failures: list[str] = []
    else:
        failures = cleanup_managed_directory(
            context,
            label="npm npx workspaces",
            path=npx_path,
            max_bytes=NPM_NPX_CACHE_MAX_BYTES,
            max_age_days=STALE_NPX_CACHE_MAX_AGE_DAYS,
            process_names=("npm", "npx"),
            dry_run=dry_run,
        )
    usage, inventory_failure = inventory_cache(context, "npm", path)
    if inventory_failure:
        failures.append(inventory_failure)
    if usage is None:
        return failures
    verify_failure = run_cleanup_command(
        context,
        [executable, "cache", "verify"],
        "npm cache verification and garbage collection",
        dry_run=dry_run,
        load_nvm=True,
    )
    if verify_failure:
        failures.append(verify_failure)
    elif not dry_run:
        usage, post_verify_failure = inventory_cache(context, "npm", path)
        if post_verify_failure:
            failures.append(post_verify_failure)
    if (
        verify_failure is None
        and usage is not None
        and cache_needs_eviction(path, usage, NPM_CACHE_MAX_BYTES)
    ):
        clean_failure = run_cleanup_command(
            context,
            [executable, "cache", "clean", "--force"],
            "npm cache eviction",
            dry_run=dry_run,
            load_nvm=True,
            cache_path=path,
        )
        if clean_failure:
            failures.append(clean_failure)
    return failures


def cleanup_t3_rotated_logs(
    context: UserContext,
    *,
    dry_run: bool,
    max_bytes: int = T3_ROTATED_LOG_MAX_BYTES,
    max_age_days: int = T3_ROTATED_LOG_MAX_AGE_DAYS,
) -> list[str]:
    """Prune only numbered T3 log rotations by age and total retained size."""
    log_root = os.path.join(context.home, ".t3", "userdata", "logs")
    result = reconcile_t3_rotated_logs(
        context.home,
        context.uid,
        dry_run=dry_run,
        max_bytes=max_bytes,
        max_age_days=max_age_days,
    )
    for failure in result.errors:
        log_event(
            logger,
            "Failed to reconcile T3 rotated logs",
            level=WARNING,
            path=log_root,
            error=failure,
        )
    if not result.found_count:
        return list(result.errors)

    log_event(
        logger,
        "T3 rotated log inventory",
        path=log_root,
        rotated_count=result.found_count,
        rotated_size_mb=round(result.found_bytes / BYTES_PER_MB, 1),
        selected_count=len(result.selected),
        selected_size_mb=round(result.selected_bytes / BYTES_PER_MB, 1),
    )
    if dry_run:
        if result.selected:
            log_event(
                logger,
                "Would prune T3 rotated logs",
                path=log_root,
                removed_count=len(result.selected),
                removed_size_mb=round(result.selected_bytes / BYTES_PER_MB, 1),
            )
        return list(result.errors)

    if result.removed:
        log_event(
            logger,
            "Pruned T3 rotated logs",
            path=log_root,
            removed_count=len(result.removed),
            removed_size_mb=round(result.removed_bytes / BYTES_PER_MB, 1),
        )
    return list(result.errors)


def cleanup_codex_standalone_releases(
    context: UserContext,
    *,
    dry_run: bool,
) -> list[str]:
    """Prune excess validated Codex releases while retaining safe rollbacks."""
    result = reconcile_codex_standalone_releases(
        context.home,
        context.uid,
        dry_run=dry_run,
    )
    for failure in result.errors:
        log_event(
            logger,
            "Failed to reconcile standalone Codex releases",
            level=WARNING,
            error=failure,
        )
    if result.skipped:
        log_event(
            logger,
            "Skipped unfamiliar standalone Codex release entries",
            level=WARNING,
            skipped=", ".join(result.skipped),
        )
    if result.found:
        log_event(
            logger,
            "Standalone Codex release inventory",
            found_count=len(result.found),
            retained_count=len(result.retained),
            active_count=len(result.active),
            selected_count=len(result.selected),
        )
    if dry_run and result.selected:
        log_event(
            logger,
            "Would prune standalone Codex releases",
            selected=", ".join(result.selected),
        )
    if result.removed:
        log_event(
            logger,
            "Pruned standalone Codex releases",
            removed=", ".join(result.removed),
        )
    return list(result.errors)


def cleanup_pip_cache(context: UserContext, *, dry_run: bool) -> list[str]:
    """Purge pip's cache when oversized or its filesystem needs headroom."""
    path, executable, failure = query_cache_path(
        context,
        (["pip3", "cache", "dir"], ["pip", "cache", "dir"]),
        "pip cache path query",
    )
    if failure:
        return [failure]
    if path is None or executable is None:
        return []
    if defer_active_cache(path, ("pip", "pip3")):
        return []

    usage, inventory_failure = inventory_cache(context, "pip", path)
    failures = [inventory_failure] if inventory_failure else []
    if usage is not None and cache_needs_eviction(path, usage, PIP_CACHE_MAX_BYTES):
        cleanup_failure = run_cleanup_command(
            context,
            [executable, "cache", "purge"],
            "pip cache eviction",
            dry_run=dry_run,
            cache_path=path,
        )
        if cleanup_failure:
            failures.append(cleanup_failure)
    return failures


def cleanup_uv_cache(context: UserContext, *, dry_run: bool) -> list[str]:
    """Use uv's supported periodic pruning operation."""
    path, executable, failure = query_cache_path(
        context,
        (["uv", "cache", "dir"],),
        "uv cache path query",
    )
    if failure:
        return [failure]
    if path is None or executable is None:
        return []
    if defer_active_cache(path, ("uv",)):
        return []

    usage, inventory_failure = inventory_cache(context, "uv", path)
    failures = [inventory_failure] if inventory_failure else []
    if usage is None:
        return failures
    cleanup_failure = run_cleanup_command(
        context,
        [executable, "cache", "prune"],
        "uv cache prune",
        dry_run=dry_run,
    )
    if cleanup_failure:
        failures.append(cleanup_failure)
    return failures


def cleanup_go_cache(
    context: UserContext,
    *,
    cache_name: str,
    go_env_name: str,
    max_bytes: int,
    clean_args: list[str],
    dry_run: bool,
) -> list[str]:
    """Evict one idle Go cache when oversized or short of filesystem space."""
    path, executable, failure = query_cache_path(
        context,
        (["go", "env", go_env_name],),
        f"{cache_name} path query",
    )
    if failure:
        return [failure]
    if path is None or executable is None:
        return []
    if defer_active_cache(path, ("go", "compile", "link", "gopls")):
        return []

    usage, inventory_failure = inventory_cache(context, cache_name, path)
    failures = [inventory_failure] if inventory_failure else []
    if usage is not None and cache_needs_eviction(path, usage, max_bytes):
        cleanup_failure = run_cleanup_command(
            context,
            [executable, "clean"] + clean_args,
            f"{cache_name} eviction",
            dry_run=dry_run,
            cache_path=path,
        )
        if cleanup_failure:
            failures.append(cleanup_failure)
    return failures


def cleanup_agent_caches(context: UserContext, *, dry_run: bool) -> list[str]:
    """Clean only explicitly rebuildable Codex and OpenCode paths."""
    # Do not trust the service process environment for paths. This function
    # may be called from a test, a manually launched helper, or a unit with
    # inherited environment overrides; cache cleanup must remain user-scoped.
    xdg_cache_home = os.path.join(context.home, ".cache")
    codex_home = os.path.join(context.home, ".codex")
    failures = cleanup_managed_directory(
        context,
        label="OpenCode",
        path=os.path.join(xdg_cache_home, "opencode"),
        max_bytes=OPENCODE_CACHE_MAX_BYTES,
        max_age_days=STALE_USER_TOOL_CACHE_MAX_AGE_DAYS,
        process_names=("opencode", "opencode-cli"),
        dry_run=dry_run,
    )
    failures.extend(
        cleanup_managed_directory(
            context,
            label="Codex",
            path=os.path.join(codex_home, "cache"),
            max_bytes=CODEX_CACHE_MAX_BYTES,
            max_age_days=STALE_USER_TOOL_CACHE_MAX_AGE_DAYS,
            process_names=("codex",),
            dry_run=dry_run,
        )
    )
    failures.extend(
        cleanup_stale_children(
            context,
            label="Codex temporary files",
            path=os.path.join(codex_home, "tmp"),
            max_age_days=STALE_USER_TOOL_TMP_MAX_AGE_DAYS,
            process_names=("codex",),
            dry_run=dry_run,
        )
    )
    return failures


def cleanup_electron_downloads(context: UserContext, *, dry_run: bool) -> list[str]:
    """Expire recognized Electron download ZIPs, preserving installations and profiles."""
    root = os.path.join(context.home, ".cache", "electron")
    if not is_safe_managed_path(context, root, "Electron downloads") or not os.path.isdir(root):
        return []
    if defer_active_cache(root, ("npm", "npx", "pnpm", "yarn", "electron")):
        return []
    pattern = re.compile(r"electron-v[0-9]+\.[0-9]+\.[0-9]+-linux-(?:x64|arm64|armv7l)\.zip")
    failures: list[str] = []
    try:
        candidates: list[tuple[float, str]] = []
        for directory, children, files in os.walk(root):
            children[:] = [name for name in children if not os.path.islink(os.path.join(directory, name))]
            for name in files:
                if pattern.fullmatch(name):
                    path = os.path.join(directory, name)
                    info = os.lstat(path)
                    if stat.S_ISREG(info.st_mode) and info.st_uid == context.uid:
                        candidates.append((info.st_mtime, path))
        for _mtime, path in sorted(candidates):
            if not is_safe_managed_path(context, path, "Electron download"):
                continue
            info = os.lstat(path)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != context.uid:
                continue
            age_days = (time.time() - info.st_mtime) / 86400
            # Even under pressure, leave newly downloaded/in-progress artifacts alone.
            if age_days < 1 or (age_days < 30 and not storage_pressure(path)):
                continue
            if defer_active_cache(path, ("npm", "npx", "pnpm", "yarn", "electron")):
                continue
            if not dry_run:
                os.unlink(path)
            log_event(
                logger, "Would remove Electron download" if dry_run else "Removed Electron download",
                path=path, size_mb=round(info.st_size / BYTES_PER_MB, 1),
            )
    except OSError as exc:
        failures.append(f"Electron download cleanup: {exc}")
    return failures


def _t3_state(context: UserContext, path: str) -> JSONDict:
    """Read only a bounded regular T3 protocol-2 state file with known versions."""
    if not is_safe_managed_path(context, path, "T3 service state"):
        raise ValueError("unsafe T3 state path")
    info = os.lstat(path)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != context.uid or info.st_size > 65536:
        raise ValueError("unrecognized T3 state file")
    with open(path, encoding="utf-8") as handle:
        state = json.load(handle)
    if not isinstance(state, dict) or state.get("protocol") != 2:
        raise ValueError("unrecognized T3 state protocol")
    if (
        not isinstance(state.get("activeVersion"), str)
        or not _T3_VERSION_PATTERN.fullmatch(state["activeVersion"])
    ):
        raise ValueError("unrecognized T3 active version")
    if "update" in state:
        update = state["update"]
        if not isinstance(update, dict) or update.get("status") not in (
            "pending", "committed", "failed", "rolled-back",
        ):
            raise ValueError("unrecognized T3 update state")
        for key in ("fromVersion", "targetVersion"):
            if not isinstance(update.get(key), str) or not _T3_VERSION_PATTERN.fullmatch(update[key]):
                raise ValueError("unrecognized T3 update version")
        expected_active = update["targetVersion"] if update["status"] == "committed" else update["fromVersion"]
        if (
            state["activeVersion"] != expected_active
            or _t3_version_key(update["targetVersion"]) <= _t3_version_key(update["fromVersion"])
        ):
            raise ValueError("inconsistent T3 update state")
    return state


def _t3_version_key(value: str) -> tuple[int, ...]:
    """Order the validated stable versions understood by this retention policy."""
    return tuple(int(part) for part in value.split("."))


def _validated_t3_runtime(
    context: UserContext,
    path: str,
    version: str,
) -> os.stat_result | None:
    """Validate one upstream T3 runtime without following user-owned links."""

    sentinel = os.path.join(path, ".install-complete")
    native_binary = os.path.join(path, "t3")
    legacy_manifest = os.path.join(path, "node_modules", "t3", "package.json")
    legacy_binary = os.path.join(path, "node_modules", "t3", "dist", "bin.mjs")
    if not all(
        is_safe_managed_path(context, item, "T3 runtime")
        for item in (path, sentinel)
    ):
        return None
    try:
        info = os.lstat(path)
        sentinel_info = os.lstat(sentinel)
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != context.uid
            or not stat.S_ISREG(sentinel_info.st_mode)
            or sentinel_info.st_uid != context.uid
            or sentinel_info.st_size > 256
        ):
            return None

        if os.path.lexists(native_binary):
            if not is_safe_managed_path(context, native_binary, "T3 runtime"):
                return None
            binary_info = os.lstat(native_binary)
            if (
                not stat.S_ISREG(binary_info.st_mode)
                or binary_info.st_uid != context.uid
                or not os.access(native_binary, os.X_OK)
            ):
                return None
            with open(sentinel, encoding="utf-8") as handle:
                return info if handle.read().strip() == version else None

        if not all(
            is_safe_managed_path(context, item, "T3 runtime")
            for item in (legacy_manifest, legacy_binary)
        ):
            return None
        manifest_info = os.lstat(legacy_manifest)
        binary_info = os.lstat(legacy_binary)
        if (
            not stat.S_ISREG(manifest_info.st_mode)
            or manifest_info.st_uid != context.uid
            or manifest_info.st_size > 65536
            or not stat.S_ISREG(binary_info.st_mode)
            or binary_info.st_uid != context.uid
        ):
            return None
        with open(legacy_manifest, encoding="utf-8") as handle:
            manifest = json.load(handle)
        if (
            not isinstance(manifest, dict)
            or manifest.get("name") != "t3"
            or manifest.get("version") != version
        ):
            return None
    except (OSError, ValueError):
        return None
    return info


def cleanup_t3_runtimes(context: UserContext, *, dry_run: bool) -> list[str]:
    """Prune validated old T3 runtimes while retaining rollback and process references."""
    root = os.path.join(context.home, ".t3", "runtime", "versions")
    state_path = os.path.join(context.home, ".t3", "runtime", "service-state.json")
    if not os.path.lexists(root):
        return []
    if not is_safe_managed_path(context, root, "T3 runtimes"):
        return []
    try:
        state = _t3_state(context, state_path)
        update = state.get("update", {})
        if update.get("status") == "pending" or tool_is_active(("npm", "npx", "pnpm", "yarn")):
            log_event(logger, "T3 runtime cleanup deferred during update")
            return []
        active = state["activeVersion"]
        retained = {active, update.get("fromVersion"), update.get("targetVersion")}
        candidates: dict[str, os.stat_result] = {}
        for name in os.listdir(root):
            if not _T3_VERSION_PATTERN.fullmatch(name):
                continue
            path = os.path.join(root, name)
            info = _validated_t3_runtime(context, path, name)
            if info is None:
                continue
            candidates[name] = info
        if active not in candidates:
            raise ValueError("active T3 runtime could not be validated")
        older = sorted(
            (name for name in candidates if _t3_version_key(name) < _t3_version_key(active)),
            key=_t3_version_key,
        )
        if older:
            retained.add(older[-1])
        for name in older:
            if name in retained:
                continue
            if time.time() - candidates[name].st_mtime < 7 * 86400:
                continue
            path = os.path.join(root, name)
            usage = cache_usage(path)
            if usage.newest_mtime is None or time.time() - usage.newest_mtime < 7 * 86400:
                continue
            if process_uses_path(path):
                log_event(logger, "T3 runtime retained while in use", version=name)
                continue
            if _t3_state(context, state_path) != state:
                raise ValueError("T3 state changed during cleanup")
            current = os.lstat(path)
            if (current.st_dev, current.st_ino) != (candidates[name].st_dev, candidates[name].st_ino):
                raise ValueError("T3 runtime changed during cleanup")
            if not dry_run:
                shutil.rmtree(path)
            log_event(
                logger, "Would remove old T3 runtime" if dry_run else "Removed old T3 runtime",
                version=name, size_mb=round(usage.size_bytes / BYTES_PER_MB, 1),
            )
    except (OSError, ValueError) as exc:
        return [f"T3 runtime cleanup: {exc}"]
    return []


def cleanup_agent_storage(context: UserContext, *, dry_run: bool) -> list[str]:
    """Reconcile validated agent releases and numbered T3 log rotations."""
    failures = cleanup_codex_standalone_releases(context, dry_run=dry_run)
    failures.extend(cleanup_t3_rotated_logs(context, dry_run=dry_run))
    failures.extend(cleanup_t3_runtimes(context, dry_run=dry_run))
    return failures


def run_user_cache_maintenance(context: UserContext, *, dry_run: bool) -> list[str]:
    """Run each independent user cache policy and collect failures."""
    failures = cleanup_agent_storage(context, dry_run=dry_run)
    failures.extend(cleanup_electron_downloads(context, dry_run=dry_run))
    failures.extend(cleanup_uv_cache(context, dry_run=dry_run))
    failures.extend(cleanup_npm_cache(context, dry_run=dry_run))
    failures.extend(cleanup_pip_cache(context, dry_run=dry_run))
    failures.extend(
        cleanup_go_cache(
            context,
            cache_name="Go build cache",
            go_env_name="GOCACHE",
            max_bytes=GO_BUILD_CACHE_MAX_BYTES,
            clean_args=["-cache", "-testcache", "-fuzzcache"],
            dry_run=dry_run,
        )
    )
    failures.extend(
        cleanup_go_cache(
            context,
            cache_name="Go module cache",
            go_env_name="GOMODCACHE",
            max_bytes=GO_MODULE_CACHE_MAX_BYTES,
            clean_args=["-modcache"],
            dry_run=dry_run,
        )
    )
    failures.extend(cleanup_agent_caches(context, dry_run=dry_run))
    return failures


def main(argv: list[str] | None = None) -> int:
    """Run bounded user cache maintenance."""
    args = parse_args(argv)
    operation = (
        "agent storage reconciliation"
        if args.agent_storage_only
        else "user cache maintenance"
    )
    try:
        context = resolve_user_context()
    except (KeyError, ValueError) as exc:
        log_event(
            logger,
            "Could not resolve cache-maintenance user",
            level=WARNING,
            error=str(exc),
        )
        return 1

    if context.uid == 0:
        log_event(logger, f"Skipping {operation} for the root account")
        return 0

    log_event(
        logger,
        f"Starting {operation}",
        username=context.username,
        dry_run=args.dry_run,
    )
    if args.agent_storage_only:
        failures = cleanup_agent_storage(context, dry_run=args.dry_run)
    else:
        failures = run_user_cache_maintenance(context, dry_run=args.dry_run)
    if failures:
        log_event(
            logger,
            f"{operation.capitalize()} failed",
            level=WARNING,
            failure_count=len(failures),
            details="\n".join(failures),
        )
        return 1

    log_event(
        logger,
        f"{operation.capitalize()} completed successfully",
        username=context.username,
        dry_run=args.dry_run,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
