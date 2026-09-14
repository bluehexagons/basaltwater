#!/usr/bin/env python3
"""
Auto-update Node.js

This script updates Node.js via nvm on the LTS track by default. Global npm
package upgrades are opt-in by policy. If a non-LTS/latest Node.js track is
already installed, it is kept current as an explicit user opt-in.

Logs to: /var/log/infra_tools/web/auto_update_node.log
"""

from __future__ import annotations

import os
import shlex
import sys
import subprocess
import pwd
import json
import re
from logging import ERROR

# Add lib directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '../..'))

from lib.logging_utils import get_service_logger
from lib.logging_utils import log_event
from lib.logging_utils import log_subprocess_result
from lib.notifications import load_notification_configs_from_state, send_notification_safe
from lib.types import MaybeStr
from lib.update_policy import (
    ECOSYSTEM_AUTO_UPGRADE_ENV,
    ecosystem_auto_upgrade_enabled,
    npm_freshness_args,
)
from lib.remote_utils import CommandTimeoutError, run as run_command

# Initialize centralized logger
logger = get_service_logger('auto_update_node', 'web', use_syslog=True)

_NODE_VERSION_RE = re.compile(r"v(\d+)\.(\d+)\.(\d+)")
_NVM_COMMAND_TIMEOUT_SECONDS = 30 * 60


def get_nvm_dir() -> str:
    """Get the NVM_DIR path for the current user."""
    # Get the effective user running this process (systemd User= sets this)
    username = pwd.getpwuid(os.getuid()).pw_name
    home_dir = pwd.getpwnam(username).pw_dir
    return os.path.join(home_dir, '.nvm')


def run_nvm_command(args: list[str]) -> subprocess.CompletedProcess[str]:
    """Run a command with nvm environment loaded."""
    nvm_dir = get_nvm_dir()
    home_dir = os.path.dirname(nvm_dir)
    pw_entry = pwd.getpwuid(os.getuid())
    full_cmd = (
        f'export NVM_DIR={shlex.quote(nvm_dir)} && '
        '[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh" && '
        f'{shlex.join(args)}'
    )

    command = ["/bin/bash", "-lc", full_cmd]
    try:
        return run_command(
            command,
            capture_output=True,
            text=True,
            cwd=home_dir,
            timeout=_NVM_COMMAND_TIMEOUT_SECONDS,
            env={
                **os.environ,
                "HOME": home_dir,
                "USER": pw_entry.pw_name,
                "LOGNAME": pw_entry.pw_name,
            },
        )
    except CommandTimeoutError as exc:
        return subprocess.CompletedProcess(
            command,
            124,
            "",
            str(exc),
        )


def normalize_node_version(value: str) -> str:
    """Return the first concrete Node.js version found in nvm output."""
    match = _NODE_VERSION_RE.search(value)
    return match.group(0) if match else ""


def node_version_key(version: str) -> tuple[int, int, int]:
    """Return a comparable semantic version key for a normalized Node.js version."""
    match = _NODE_VERSION_RE.fullmatch(version)
    if not match:
        return (0, 0, 0)
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def node_major(version: str) -> int | None:
    """Return the major version for a normalized Node.js version."""
    match = _NODE_VERSION_RE.fullmatch(version)
    return int(match.group(1)) if match else None


def get_current_lts_version() -> str:
    """Get the latest LTS version available."""
    result = run_nvm_command(["nvm", "version-remote", "--lts"])
    if result.returncode == 0:
        return normalize_node_version(result.stdout)
    return ""


def get_latest_version() -> str:
    """Get the latest non-LTS stable version available."""
    result = run_nvm_command(["nvm", "version-remote", "node"])
    if result.returncode == 0:
        return normalize_node_version(result.stdout)
    return ""


def get_current_version() -> str:
    """Get the currently installed default version."""
    result = run_nvm_command(["nvm", "version", "default"])
    if result.returncode == 0:
        return normalize_node_version(result.stdout)
    return ""


def get_installed_versions() -> list[str]:
    """Return installed Node.js versions reported by nvm."""
    result = run_nvm_command(["nvm", "ls", "--no-colors"])
    if result.returncode != 0:
        return []

    versions: list[str] = []
    seen: set[str] = set()
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if "->" in stripped and not stripped.startswith("->"):
            continue
        match = re.match(r"(?:->\s*)?(v\d+\.\d+\.\d+)", stripped)
        if not match:
            continue
        normalized = match.group(1)
        if normalized not in seen:
            versions.append(normalized)
            seen.add(normalized)
    return versions


def select_installed_lts_version(installed_versions: list[str], current_lts: str) -> str:
    """Return the newest installed version on the current LTS major."""
    lts_major = node_major(current_lts)
    if lts_major is None:
        return ""
    candidates = [version for version in installed_versions if node_major(version) == lts_major]
    return max(candidates, key=node_version_key, default="")


def select_installed_latest_track_version(
    installed_versions: list[str],
    current_lts: str,
    latest_version: str,
) -> str:
    """Return the newest installed non-LTS version when a latest track exists."""
    lts_major = node_major(current_lts)
    latest_major = node_major(latest_version)
    if lts_major is None:
        return ""
    if latest_major is not None and latest_major <= lts_major:
        return ""

    candidates = [version for version in installed_versions if (node_major(version) or 0) > lts_major]
    return max(candidates, key=node_version_key, default="")


def set_default_lts_alias() -> bool:
    """Ensure nvm defaults interactive shells to the LTS track."""
    result = run_nvm_command(["nvm", "alias", "default", "lts/*"])
    return log_subprocess_result(logger, "Set Node.js default alias to LTS", result, failure_level=ERROR)


def get_global_package_specs(source_version: str) -> tuple[bool, list[str], MaybeStr]:
    """Return exact global package specs installed under a Node.js version."""
    result = run_nvm_command([
        "nvm",
        "exec",
        source_version,
        "npm",
        "list",
        "-g",
        "--depth=0",
        "--json",
    ])
    if result.returncode != 0:
        details = result.stderr.strip() or f"npm list failed for {source_version}"
        return False, [], details

    # ``nvm exec`` writes a human-readable ``Running node ...`` line before
    # the command's output.  Decode the first complete JSON value after that
    # wrapper, while rejecting any non-whitespace output after the inventory.
    decoder = json.JSONDecoder()
    json_offset = result.stdout.find("{")
    try:
        if json_offset < 0:
            raise json.JSONDecodeError("no JSON object found", result.stdout, 0)
        payload, end = decoder.raw_decode(result.stdout[json_offset:])
        trailing = result.stdout[json_offset + end :].strip()
        if trailing:
            raise json.JSONDecodeError(
                "unexpected output after package inventory",
                result.stdout,
                json_offset + end,
            )
    except json.JSONDecodeError as exc:
        details = exc
        return False, [], f"Failed to parse global npm packages for {source_version}: {details}"

    if not isinstance(payload, dict) or payload.get("error") or payload.get("problems"):
        return False, [], f"Invalid global npm package inventory for {source_version}"

    dependencies = payload.get("dependencies", {})
    if not isinstance(dependencies, dict):
        return False, [], f"Invalid global npm dependencies for {source_version}"

    package_specs: list[str] = []
    missing_versions: list[str] = []
    for name, metadata in dependencies.items():
        if name == "npm":
            continue
        version = metadata.get("version") if isinstance(metadata, dict) else None
        if not isinstance(name, str) or not isinstance(version, str) or not version.strip():
            missing_versions.append(str(name))
            continue
        if any(character.isspace() or ord(character) < 32 for character in name + version):
            missing_versions.append(name)
            continue
        package_specs.append(f"{name}@{version}")

    if missing_versions:
        return (
            False,
            [],
            "Global npm packages missing safe version metadata: " + ", ".join(sorted(missing_versions)),
        )
    return True, sorted(package_specs), None


def reinstall_global_packages(source_version: str, target_version: str) -> tuple[bool, MaybeStr]:
    """Preserve exact global package versions across a Node.js update."""
    package_listed, package_specs, package_error = get_global_package_specs(source_version)
    if not package_listed:
        return False, package_error

    if not package_specs:
        return True, None

    command = ["nvm", "exec", target_version, "npm", "install", "-g"] + package_specs
    action = f"Preserved global npm packages for Node.js {target_version}"
    result = run_nvm_command(command)
    if not log_subprocess_result(logger, action, result, failure_level=ERROR):
        details = result.stderr.strip() or result.stdout.strip() or shlex.join(command)
        return False, f"{action}: {details}"
    return True, None


def rollback_target_version(
    update_track: str,
    target_version: str,
    source_version: str,
) -> tuple[bool, MaybeStr]:
    """Restore the previous runtime after a new version cannot be completed."""
    normalized_target = normalize_node_version(target_version)
    normalized_source = normalize_node_version(source_version)
    if not normalized_target or not normalized_source:
        return False, "Could not identify the Node.js versions needed for rollback"
    if update_track not in {"lts", "latest"}:
        return False, f"Unsupported Node.js update track for rollback: {update_track}"
    if normalized_target == normalized_source:
        return False, "Refusing to remove the active Node.js source version"

    if update_track == "lts":
        alias_command = ["nvm", "alias", "default", normalized_source]
        alias_result = run_nvm_command(alias_command)
        alias_action = f"Restored Node.js default alias to {normalized_source}"
        if not log_subprocess_result(
            logger,
            alias_action,
            alias_result,
            failure_level=ERROR,
        ):
            details = (
                alias_result.stderr.strip()
                or alias_result.stdout.strip()
                or shlex.join(alias_command)
            )
            return (
                False,
                f"{alias_action} failed; retained {normalized_target}: {details}",
            )

    uninstall_command = ["nvm", "uninstall", normalized_target]
    uninstall_result = run_nvm_command(uninstall_command)
    uninstall_action = f"Removed incomplete Node.js {normalized_target}"
    if not log_subprocess_result(
        logger,
        uninstall_action,
        uninstall_result,
        failure_level=ERROR,
    ):
        details = (
            uninstall_result.stderr.strip()
            or uninstall_result.stdout.strip()
            or shlex.join(uninstall_command)
        )
        return False, f"{uninstall_action} failed: {details}"
    return True, None


def install_target_version(
    update_track: str,
    target_version: str,
    source_version: str = "",
) -> tuple[bool, MaybeStr]:
    """Install the latest Node.js version for a track and migrate global packages."""
    result = run_nvm_command(["nvm", "install", target_version])
    action = "Installed latest Node.js version" if update_track == "latest" else "Installed latest Node.js LTS"
    if not log_subprocess_result(logger, action, result, failure_level=ERROR):
        details = result.stderr.strip() or result.stdout.strip() or action
        return False, details

    if update_track == "lts" and not set_default_lts_alias():
        rolled_back, rollback_error = rollback_target_version(
            update_track,
            target_version,
            source_version,
        )
        details = "Failed to set nvm default alias to LTS"
        if rolled_back:
            details += "; restored the previous Node.js runtime"
        elif rollback_error:
            details += f"; rollback failed: {rollback_error}"
        return False, details

    if source_version and normalize_node_version(source_version) != normalize_node_version(target_version):
        migrated, migration_error = reinstall_global_packages(
            source_version,
            target_version,
        )
        if not migrated:
            rolled_back, rollback_error = rollback_target_version(
                update_track,
                target_version,
                source_version,
            )
            details = migration_error or "Global npm package migration failed"
            if rolled_back:
                details += "; restored the previous Node.js runtime"
            elif rollback_error:
                details += f"; rollback failed: {rollback_error}"
            return False, details

    return True, None


def cleanup_old_versions(candidates: list[str], keep_versions: set[str]) -> tuple[bool, MaybeStr]:
    """Remove superseded nvm Node.js versions after package migration succeeds."""
    failures: list[str] = []
    normalized_keep = {normalize_node_version(version) for version in keep_versions if normalize_node_version(version)}
    for version in sorted({normalize_node_version(candidate) for candidate in candidates if normalize_node_version(candidate)}):
        if version in normalized_keep:
            continue
        result = run_nvm_command(["nvm", "uninstall", version])
        if not log_subprocess_result(logger, f"Removed outdated Node.js {version}", result, failure_level=ERROR):
            details = result.stderr.strip() or result.stdout.strip() or f"nvm uninstall {version} failed"
            failures.append(f"{version}: {details}")

    if failures:
        return False, "\n".join(failures)
    return True, None


def update_global_packages() -> tuple[bool, MaybeStr]:
    """Update npm itself and global npm packages."""
    if not ecosystem_auto_upgrade_enabled():
        log_event(
            logger,
            "Node.js global package auto-upgrades disabled by policy",
            env_var=ECOSYSTEM_AUTO_UPGRADE_ENV,
        )
        return True, None

    commands = (
        ("Updated npm", ["npm", "install", "-g", "npm@latest"] + npm_freshness_args()),
        ("Updated global npm packages", ["npm", "update", "-g"] + npm_freshness_args()),
        ("Updated pnpm", ["npm", "install", "-g", "pnpm"] + npm_freshness_args()),
    )
    failures: list[str] = []

    for action, command in commands:
        result = run_nvm_command(command)
        if not log_subprocess_result(logger, action, result):
            details = result.stderr.strip() or result.stdout.strip() or command
            failures.append(f"{action}: {details}")

    if failures:
        return False, "\n".join(failures)
    return True, None


def main() -> int:
    """Main function to update Node.js."""
    log_event(logger, "Starting Node.js update check")
    
    nvm_dir = get_nvm_dir()
    
    # Load notification configs from saved machine state
    notification_configs = load_notification_configs_from_state(logger)
    
    if not os.path.exists(nvm_dir):
        log_event(logger, "nvm directory not found", level=ERROR, nvm_dir=nvm_dir)
        send_notification_safe(
            notification_configs,
            subject="Error: Node.js update failed",
            job="auto_update_node",
            status="error",
            message=f"nvm not found at {nvm_dir}",
            logger=logger
        )
        return 1
    
    current_lts = get_current_lts_version()
    latest_version = get_latest_version()
    current_version = get_current_version()
    installed_versions = get_installed_versions()
    if current_version and current_version not in installed_versions:
        installed_versions.append(current_version)

    if not current_lts:
        log_event(logger, "Failed to get latest LTS version", level=ERROR)
        send_notification_safe(
            notification_configs,
            subject="Error: Node.js update failed",
            job="auto_update_node",
            status="error",
            message="Failed to get latest LTS version",
            logger=logger
        )
        return 1

    installed_lts = select_installed_lts_version(installed_versions, current_lts)
    installed_latest = select_installed_latest_track_version(installed_versions, current_lts, latest_version)
    cleanup_candidates: list[str] = []
    keep_versions = {current_lts}
    latest_status = "not installed"

    lts_source = installed_lts or current_version
    if installed_lts == current_lts:
        log_event(
            logger,
            "Node.js LTS already up to date",
            current_version=installed_lts,
            target_version=current_lts,
            update_track="LTS",
        )
        if not set_default_lts_alias():
            send_notification_safe(
                notification_configs,
                subject="Error: Node.js update failed",
                job="auto_update_node",
                status="error",
                message="Failed to set Node.js default alias to LTS",
                logger=logger,
            )
            return 1
    else:
        log_event(
            logger,
            "Updating Node.js LTS",
            current_version=installed_lts or current_version,
            target_version=current_lts,
            update_track="LTS",
        )
        installed, install_error = install_target_version("lts", current_lts, lts_source)
        if not installed:
            log_event(
                logger,
                "Node.js LTS update failed",
                level=ERROR,
                current_version=installed_lts or current_version,
                target_version=current_lts,
                update_track="LTS",
            )
            send_notification_safe(
                notification_configs,
                subject="Error: Node.js update failed",
                job="auto_update_node",
                status="error",
                message=f"Failed to update Node.js LTS to {current_lts}",
                details=install_error,
                logger=logger,
            )
            return 1
        if lts_source:
            cleanup_candidates.append(lts_source)

    if installed_latest:
        if not latest_version:
            log_event(logger, "Failed to get latest Node.js version", level=ERROR, update_track="latest")
            send_notification_safe(
                notification_configs,
                subject="Error: Node.js update failed",
                job="auto_update_node",
                status="error",
                message="Failed to get latest Node.js version",
                logger=logger,
            )
            return 1
        keep_versions.add(latest_version)
        if installed_latest == latest_version:
            latest_status = f"already up to date ({latest_version})"
            log_event(
                logger,
                "Node.js latest track already up to date",
                current_version=installed_latest,
                target_version=latest_version,
                update_track="latest",
            )
        else:
            latest_status = f"updated to {latest_version}"
            log_event(
                logger,
                "Updating installed Node.js latest track",
                current_version=installed_latest,
                target_version=latest_version,
                update_track="latest",
            )
            installed, install_error = install_target_version("latest", latest_version, installed_latest)
            if not installed:
                log_event(
                    logger,
                    "Node.js latest-track update failed",
                    level=ERROR,
                    current_version=installed_latest,
                    target_version=latest_version,
                    update_track="latest",
                )
                send_notification_safe(
                    notification_configs,
                    subject="Error: Node.js update failed",
                    job="auto_update_node",
                    status="error",
                    message=f"Failed to update installed Node.js latest track to {latest_version}",
                    details=install_error,
                    logger=logger,
                )
                return 1
            cleanup_candidates.append(installed_latest)
    else:
        log_event(logger, "No installed Node.js latest track detected")

    cleaned, cleanup_error = cleanup_old_versions(cleanup_candidates, keep_versions)
    if not cleaned:
        log_event(logger, "Node.js outdated version cleanup failed", level=ERROR)
        send_notification_safe(
            notification_configs,
            subject="Error: Node.js update failed",
            job="auto_update_node",
            status="error",
            message="Failed to clean up outdated Node.js versions",
            details=cleanup_error,
            logger=logger
        )
        return 1

    packages_updated, package_error = update_global_packages()
    if not packages_updated:
        log_event(
            logger,
            "Node.js global package update failed",
            level=ERROR,
            current_version=get_current_version() or current_lts,
            target_version=current_lts,
            update_track="LTS",
        )
        send_notification_safe(
            notification_configs,
            subject="Error: Node.js update failed",
            job="auto_update_node",
            status="error",
            message="Failed to update global Node.js packages",
            details=package_error,
            logger=logger
        )
        return 1

    # Re-read the current version after any successful install so notifications reflect
    # the actual installed Node.js version rather than the pre-update version.
    post_update_version = get_current_version() or current_lts
    
    log_event(
        logger,
        "Node.js update tasks completed successfully",
        current_version=post_update_version,
        target_version=current_lts,
        update_track="LTS",
        latest_track=latest_status,
    )
    
    send_notification_safe(
        notification_configs,
        subject="Success: Node.js updated",
        job="auto_update_node",
        status="good",
        message=(
            f"Node.js LTS checked (current: {post_update_version}, target: {current_lts}); "
            f"latest track {latest_status}; "
            f"global package auto-upgrades "
            f"{'enabled' if ecosystem_auto_upgrade_enabled() else 'skipped by policy'}"
        ),
        logger=logger,
        event_type="maintenance.node_update",
        state="success",
        dedup_key="maintenance:node-update",
        delivery_policy="signal",
    )
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
