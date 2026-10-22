"""Shared systemd provisioning for recurring maintenance jobs."""

from __future__ import annotations

import os
import re
from typing import Optional

from lib.remote_utils import is_dry_run
from lib.unit_transaction import replace_units
from lib.systemd_service import SYSTEMD_DIR
from lib.validation import validate_filesystem_path, validate_service_name_uniqueness
from lib.validators import validate_username


_ENVIRONMENT_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _validate_unit_value(value: str, name: str) -> None:
    """Reject values that could add unintended systemd directives."""
    if not value or any(character in value for character in ("\n", "\r", "\0")):
        raise ValueError(f"{name} must be non-empty and contain no control characters")


def _quote_unit_value(value: str) -> str:
    """Quote one literal word without allowing systemd specifier expansion."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
    return f'"{escaped}"'


def configure_maintenance_timer(
    *,
    service_name: str,
    service_desc: str,
    timer_desc: str,
    script_path: str,
    script_args: tuple[str, ...] = (),
    isolated_python: bool = False,
    schedule: Optional[str],
    check_name: str,
    check_path: Optional[str] = None,
    user: Optional[str] = None,
    environment: Optional[dict[str, str]] = None,
    sandbox_user_service: bool = False,
    writable_paths: Optional[tuple[str, ...]] = None,
    randomized_delay: str = "30min",
    timeout: str = "4h",
    on_boot_sec: Optional[str] = None,
    persistent: bool = True,
    network_online: bool = True,
    purpose: str = "maintenance",
) -> bool:
    """Install, enable, and verify a systemd timer for a maintenance script.

    Existing units are replaced atomically and remain enabled while their new
    definitions are staged. This avoids the maintenance gap caused by deleting
    working units before replacements have been written and loaded.
    """
    validate_service_name_uniqueness(service_name, [])
    validate_filesystem_path(script_path, must_exist=False)
    if not os.path.isabs(script_path):
        raise ValueError(f"Maintenance script path must be absolute: {script_path}")
    if not isinstance(script_args, tuple):
        raise ValueError("Maintenance script arguments must be a tuple of strings")
    for argument in script_args:
        if (not isinstance(argument, str) or not argument
                or any(ord(character) < 32 or ord(character) == 127 for character in argument)):
            raise ValueError("Maintenance script arguments must be non-empty strings without control characters")
    if type(isolated_python) is not bool:
        raise ValueError("Isolated Python selection must be a boolean")

    for value, name in (
        (service_desc, "service description"),
        (timer_desc, "timer description"),
        (check_name, "maintenance name"),
        (randomized_delay, "randomized delay"),
        (timeout, "service timeout"),
        (purpose, "maintenance purpose"),
    ):
        _validate_unit_value(value, name)
    if schedule:
        _validate_unit_value(schedule, "timer schedule")
    if on_boot_sec:
        _validate_unit_value(on_boot_sec, "boot delay")
    if not schedule and not on_boot_sec:
        raise ValueError("Maintenance timer requires a calendar or boot trigger")

    if check_path:
        validate_filesystem_path(check_path, must_exist=False)
        if not os.path.isabs(check_path):
            raise ValueError(f"Maintenance prerequisite path must be absolute: {check_path}")
        if not os.path.exists(check_path):
            print(f"  ℹ {check_name} not installed, skipping {purpose} configuration")
            return True

    user_line = ""
    if user:
        if not validate_username(user):
            raise ValueError(f"Invalid service username: {user}")
        user_line = f"User={user}\n"

    if writable_paths and not sandbox_user_service:
        raise ValueError("Writable paths require a sandboxed user service")
    if sandbox_user_service and not user:
        raise ValueError("A sandboxed user service requires a service username")
    sandbox_lines = ""
    if sandbox_user_service:
        rendered_paths: list[str] = []
        for path in writable_paths or ():
            validate_filesystem_path(path, must_exist=False)
            if (
                not os.path.isabs(path)
                or os.path.normpath(path) != path
                or path == os.path.sep
            ):
                raise ValueError(
                    f"Sandbox writable path must be absolute and normalized: {path}"
                )
            rendered_paths.append(f"ReadWritePaths={_quote_unit_value('-' + path)}")
        sandbox_lines = "\n".join(
            (
                "UMask=0077",
                "NoNewPrivileges=true",
                "PrivateTmp=true",
                "PrivateDevices=true",
                "ProtectSystem=strict",
                "ProtectHome=read-only",
                "ProtectControlGroups=true",
                "ProtectKernelModules=true",
                "ProtectKernelTunables=true",
                "LockPersonality=true",
                "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6",
                "RestrictSUIDSGID=true",
                *rendered_paths,
                "",
            )
        )

    environment_lines = ""
    for name, value in sorted((environment or {}).items()):
        if not _ENVIRONMENT_NAME_RE.fullmatch(name):
            raise ValueError(f"Invalid environment variable name: {name}")
        _validate_unit_value(value, f"environment variable {name}")
        environment_lines += f"Environment={_quote_unit_value(f'{name}={value}')}\n"

    script_arguments = " ".join(
        _quote_unit_value(argument).replace("$", "$$")
        for argument in (script_path, *script_args)
    )
    python_command = "/usr/bin/python3 -I" if isolated_python else "/usr/bin/python3"

    network_lines = "Wants=network-online.target\nAfter=network-online.target\n" if network_online else ""
    service_content = f"""[Unit]
Description={service_desc}
Documentation=man:systemd.service(5)
{network_lines}

[Service]
Type=oneshot
{user_line}{environment_lines}{sandbox_lines}ExecStart={python_command} {script_arguments}
TimeoutStartSec={timeout}
StandardOutput=journal
StandardError=journal
"""
    trigger_lines = ""
    if on_boot_sec:
        trigger_lines += f"OnBootSec={on_boot_sec}\n"
    if schedule:
        trigger_lines += f"OnCalendar={schedule}\n"
    if persistent and schedule:
        trigger_lines += "Persistent=true\n"
    timer_content = f"""[Unit]
Description={timer_desc}
Documentation=man:systemd.timer(5)

[Timer]
{trigger_lines}AccuracySec=1min
RandomizedDelaySec={randomized_delay}

[Install]
WantedBy=timers.target
"""

    if is_dry_run():
        print(f"  [DRY-RUN] Would configure {check_name} {purpose} timer")
        return True

    try:
        replace_units(
            {f"{service_name}.service": service_content, f"{service_name}.timer": timer_content},
            activate=(f"{service_name}.timer",), unit_dir=SYSTEMD_DIR,
        )
    except Exception as exc:
        print(f"  ⚠ {check_name} {purpose} replacement failed: {exc}")
        return False

    trigger_summary = schedule or f"after boot: {on_boot_sec}"
    print(f"  ✓ {check_name} {purpose} configured ({trigger_summary})")
    return True
