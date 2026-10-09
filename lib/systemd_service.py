"""Systemd service creation for deployed applications."""

from __future__ import annotations
from typing import Optional

from lib.unit_transaction import remove_units, replace_units
from lib.validation import (
    validate_environment_variable_name,
    validate_filesystem_path,
    validate_no_control_characters,
    validate_systemd_exec_command,
    validate_service_name_uniqueness,
)


SYSTEMD_DIR = "/etc/systemd/system"


def cleanup_systemd_unit(unit_name: str, unit_type: str = "service") -> None:
    """Recoverably stop, disable, and remove a managed systemd unit.
    
    This is a low-level helper for cleaning up individual unit files.
    For most use cases, use cleanup_service() instead which automatically
    handles associated timers.
    
    Args:
        unit_name: Base name of the unit (without extension)
        unit_type: Type of unit - "service", "timer", "path", or "mount"
    """
    validate_service_name_uniqueness(unit_name, [])
    if unit_type not in {"service", "timer", "path", "mount"}:
        raise ValueError(f"Unsupported systemd unit type: {unit_type}")
    remove_units((f"{unit_name}.{unit_type}",), unit_dir=SYSTEMD_DIR)


def cleanup_service(service_name: str) -> None:
    """Remove a service and its timer/path as one recoverable unit operation.
    
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
    validate_service_name_uniqueness(service_name, [])
    remove_units(
        (f"{service_name}.timer", f"{service_name}.path", f"{service_name}.service"),
        unit_dir=SYSTEMD_DIR,
    )


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
    ``env_file`` or ``runtime_env``. basaltwater only needs the port for the
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
        f"Description={description or f'basaltwater managed service: {name}'}",
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
