"""Systemd service creation for deployed applications."""

from __future__ import annotations
import os
import re
import shlex

from typing import Optional

from lib.unit_transaction import replace_units
from lib.remote_utils import run
from lib.validation import (
    validate_environment_variable_name,
    validate_filesystem_path,
    validate_no_control_characters,
    validate_systemd_exec_command,
)


SYSTEMD_DIR = "/etc/systemd/system"


def _unit_has_install_section(unit_file: str) -> bool:
    """Return True when a unit file contains an [Install] section."""
    try:
        with open(unit_file, "r", encoding="utf-8") as f:
            return "[Install]" in f.read()
    except OSError:
        return False


def cleanup_systemd_unit(unit_name: str, unit_type: str = "service") -> None:
    """Stop, disable, and remove a single systemd unit file if it exists.
    
    This is a low-level helper for cleaning up individual unit files.
    For most use cases, use cleanup_service() instead which automatically
    handles associated timers.
    
    Args:
        unit_name: Base name of the unit (without extension)
        unit_type: Type of unit - "service", "timer", or "mount"
    """
    unit_file = os.path.join(SYSTEMD_DIR, f"{unit_name}.{unit_type}")
    
    # Stop and disable the unit
    if os.path.exists(unit_file):
        run(f"systemctl stop {shlex.quote(unit_name)}.{unit_type}", check=False)
        run(f"systemctl disable {shlex.quote(unit_name)}.{unit_type}", check=False)
        os.remove(unit_file)
        run("systemctl daemon-reload", check=False)


def cleanup_service(service_name: str) -> None:
    """Stop, disable, and remove a service plus any associated timer or path unit.
    
    This is the primary cleanup function for systemd services. It automatically
    checks for and cleans up any associated timer or path unit before cleaning
    up the service. Use this for all service cleanup operations.
    
    Args:
        service_name: Base name of the service (without .service/.timer/.path
                      extension). If the service has a timer (service_name.timer)
                      or a path activator (service_name.path), they are detected
                      and cleaned up as well.
    
    Examples:
        # Cleans up myapp.service plus myapp.timer/myapp.path if present
        cleanup_service("myapp")
        
        # Cleans up just the timer
        cleanup_service("myapp-update")
    """
    service_file = os.path.join(SYSTEMD_DIR, f"{service_name}.service")
    timer_file = os.path.join(SYSTEMD_DIR, f"{service_name}.timer")
    path_file = os.path.join(SYSTEMD_DIR, f"{service_name}.path")
    
    needs_reload = False
    
    # Stop and disable timers/paths first (they activate the service)
    for activator_file, activator_kind in (
        (timer_file, "timer"),
        (path_file, "path"),
    ):
        if os.path.exists(activator_file):
            run(f"systemctl stop {shlex.quote(service_name)}.{activator_kind}", check=False)
            run(f"systemctl disable {shlex.quote(service_name)}.{activator_kind}", check=False)
            os.remove(activator_file)
            needs_reload = True
    
    # Stop service; disable only when it declares an [Install] section
    if os.path.exists(service_file):
        run(f"systemctl stop {shlex.quote(service_name)}.service", check=False)
        if _unit_has_install_section(service_file):
            run(f"systemctl disable {shlex.quote(service_name)}.service", check=False)
        os.remove(service_file)
        needs_reload = True
    
    # Reload systemd to reflect changes
    if needs_reload:
        run("systemctl daemon-reload", check=False)


def cleanup_all_infra_services(dry_run: bool = False) -> None:
    """Remove all systemd units created by infra_tools to ensure clean deployment state.
    
    This function treats the current deployment command as the desired baseline state.
    It removes ALL previously deployed services, timers, and mounts that match
    infra_tools naming patterns, ensuring no orphaned units remain from previous
    deployments with different configurations.
    
    Args:
        dry_run: If True, only print what would be removed without actually removing
    
    Examples:
        # Clean up all infra_tools services before applying new configuration
        cleanup_all_infra_services()
        
        # Preview what would be removed (dry run)
        cleanup_all_infra_services(dry_run=True)
    """
    systemd_dir = SYSTEMD_DIR
    # Patterns for infra_tools-created units
    # These patterns match services, timers, and mounts created by various components
    infra_patterns = [
        # Unified storage operations service (new style)
        r"^storage-ops\.service$",
        r"^storage-ops\.timer$",
        r"^sync-.*\.service$",
        r"^sync-.*\.timer$",
        r"^scrub-.*\.service$",
        r"^scrub-.*\.timer$",
        r"^scrub-.*-update\.service$",
        r"^scrub-.*-update\.timer$",
        # Backup services
        r"^backup-.*\.service$",
        r"^backup-.*\.timer$",
        # Node.js app services
        r"^node-.*\.service$",
        # Manifest-defined (infra.json) service components
        r"^app-.*\.service$",
        # Auto-update units created by infra_tools. Ruby remains here only to
        # retire the obsolete unit when upgrading; no Ruby unit is recreated.
        r"^auto-update-(?:apt|godot|gogs|node|ruby|uv)\.service$",
        r"^auto-update-(?:apt|godot|gogs|node|ruby|uv)\.timer$",
        # Auto-restart service
        r"^auto-restart-if-needed\.service$",
        r"^auto-restart-if-needed\.timer$",
        # Recurring security and cleanup maintenance
        r"^security-monitor\.service$",
        r"^security-monitor\.timer$",
        r"^cleanup-maintenance\.service$",
        r"^cleanup-maintenance\.timer$",
        r"^user-cache-maintenance\.service$",
        r"^user-cache-maintenance\.timer$",
        r"^codex-auth-maintenance\.service$",
        r"^codex-auth-maintenance\.timer$",
        # SMB mount units
        r"^mnt-.*\.mount$",
        # Antistatic lobby server
        r"^antistatic\.service$",
        # Antistatic DB service
        r"^antistatic-db\.service$",
        # Gogs service
        r"^gogs\.service$",
    ]
    
    units_to_remove = []
    
    # Scan for matching unit files
    if os.path.exists(systemd_dir):
        for filename in os.listdir(systemd_dir):
            for pattern in infra_patterns:
                if re.match(pattern, filename):
                    units_to_remove.append(filename)
                    break
    
    if not units_to_remove:
        if not dry_run:
            print("  No existing infra_tools services found")
        return
    
    if dry_run:
        print(f"  [DRY RUN] Would remove {len(units_to_remove)} unit(s):")
        for unit in sorted(units_to_remove):
            print(f"    - {unit}")
        return
    
    print(f"  Cleaning up {len(units_to_remove)} existing infra_tools unit(s)...")
    
    # Group by unit type for proper stopping order
    timers = [u for u in units_to_remove if u.endswith(".timer")]
    services = [u for u in units_to_remove if u.endswith(".service")]
    mounts = [u for u in units_to_remove if u.endswith(".mount")]
    others = [u for u in units_to_remove if not any(u.endswith(ext) for ext in [".timer", ".service", ".mount"])]
    
    # Stop in order: timers first (they trigger services), then services, then mounts, then others
    for unit in timers + services + mounts + others:
        unit_type = unit.rsplit(".", 1)[1]
        unit_path = os.path.join(systemd_dir, unit)

        # Stop the unit (ignore errors if not running)
        run(f"systemctl stop {shlex.quote(unit)}", check=False)
        
        # Disable timers/mounts and services with an [Install] section.
        if unit_type in ("timer", "mount") or (
            unit_type == "service" and _unit_has_install_section(unit_path)
        ):
            run(f"systemctl disable {shlex.quote(unit)}", check=False)
        
        # Remove the file
        try:
            os.remove(unit_path)
            print(f"    ✓ Removed {unit}")
        except OSError as e:
            print(f"    ✗ Failed to remove {unit}: {e}")
    
    # Reload systemd to reflect all changes
    run("systemctl daemon-reload", check=False)
    run("systemctl reset-failed", check=False)
    
    print(f"  ✓ Cleaned up {len(units_to_remove)} unit(s)")


def _systemd_environment_line(key: str, value: str) -> str:
    """Render a validated environment value for a systemd unit line."""
    validate_environment_variable_name(key)
    validate_no_control_characters(value, f"systemd environment value for {key}")
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'Environment="{key}={escaped}"'


def generate_managed_service(name: str, exec_start: str, working_dir: str,
                             web_user: str = "www-data", web_group: str = "www-data",
                             env_file: Optional[str] = None,
                             description: Optional[str] = None,
                             runtime_env: Optional[dict[str, str]] = None,
                             writable_paths: Optional[list[str]] = None) -> str:
    """Generate a hardened systemd unit for a manifest service component.

    This makes no assumptions about the
    runtime: the component supplies its own ExecStart (a binary path or full
    command) and reads its configuration (including which port to bind) from
    ``env_file`` or ``runtime_env``. infra_tools only needs the port for the
    nginx upstream.
    """
    validate_no_control_characters(name, "systemd service name")
    validate_systemd_exec_command(exec_start)
    validate_filesystem_path(working_dir, must_exist=False)
    validate_no_control_characters(web_user, "systemd service user")
    validate_no_control_characters(web_group, "systemd service group")
    if env_file:
        validate_filesystem_path(env_file, must_exist=False)
    if description:
        validate_no_control_characters(description, "systemd service description")
    for path in writable_paths or []:
        validate_filesystem_path(path, must_exist=False)

    lines = [
        "[Unit]",
        f"Description={description or f'infra_tools managed service: {name}'}",
        "After=network.target",
        "",
        "[Service]",
        "Type=simple",
        f"User={web_user}",
        f"Group={web_group}",
        f"WorkingDirectory={working_dir}",
    ]
    if env_file:
        lines.append(f"EnvironmentFile={env_file}")
    for key, value in (runtime_env or {}).items():
        lines.append(_systemd_environment_line(key, value))
    for path in writable_paths or []:
        lines.append(f"ReadWritePaths={path}")
    lines += [
        f"ExecStart={exec_start}",
        "Restart=always",
        "RestartSec=5",
        # Default-deny filesystem writes; callers explicitly grant managed
        # persistent paths above.
        "NoNewPrivileges=true",
        "PrivateTmp=true",
        "PrivateDevices=true",
        "ProtectSystem=strict",
        "ProtectHome=true",
        "ProtectControlGroups=true",
        "ProtectKernelModules=true",
        "ProtectKernelTunables=true",
        "LockPersonality=true",
        "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6",
        "RestrictSUIDSGID=true",
        "",
        "[Install]",
        "WantedBy=multi-user.target",
        "",
    ]
    return "\n".join(lines)


def _install_and_start_unit(service_name: str, unit_content: str) -> None:
    """Write a unit file, (re)load, enable, restart, and verify it is active."""
    unit = f"{service_name}.service"
    replace_units({unit: unit_content}, activate=(unit,), unit_dir=SYSTEMD_DIR)
    print(f"  ✓ {service_name} is running")


def create_managed_service(service_name: str, exec_start: str, working_dir: str,
                           web_user: str, web_group: str,
                           env_file: Optional[str] = None,
                           description: Optional[str] = None,
                           runtime_env: Optional[dict[str, str]] = None,
                           writable_paths: Optional[list[str]] = None) -> None:
    """Create, enable, and start a manifest-defined systemd service."""
    unit_content = generate_managed_service(
        service_name, exec_start, working_dir, web_user, web_group, env_file,
        description, runtime_env, writable_paths
    )
    _install_and_start_unit(service_name, unit_content)
