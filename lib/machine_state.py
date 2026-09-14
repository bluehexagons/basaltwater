#!/usr/bin/env python3
"""Server-side state persistence for machine configuration."""

from __future__ import annotations

import os
import pwd
import shutil
import subprocess
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator, Optional

from lib.atomic_io import write_json_atomic
from lib.state_read import StateReadError, read_state_object
from lib.config import AUTO_MACHINE_TYPE, DEFAULT_MACHINE_TYPE, MACHINE_TYPES
from lib.plugin_registry import get_system_type_names
from lib.validators import validate_username


STATE_DIR = "/opt/infra_tools/state"

# Required keys for each state file
_MACHINE_STATE_REQUIRED_KEYS = ("machine_type", "system_type", "username")
# host is optional for runtime operations (services don't need it)
_SETUP_CONFIG_REQUIRED_KEYS = ("username", "system_type")
STATE_FILE = os.path.join(STATE_DIR, "machine.json")
SETUP_CONFIG_FILE = os.path.join(STATE_DIR, "setup.json")
# Scheduled jobs run as the configured account, while the full setup state is
# intentionally root-only.  Keep the small notification subset in a separate
# root-owned file so those jobs can read it without gaining access to secrets
# from the rest of setup.json.
NOTIFICATION_CONFIG_FILE = "/etc/infra-tools/notifications.json"

_LXC_VIRTUALIZATIONS = {"lxc", "lxc-libvirt", "openvz", "systemd-nspawn"}
_OCI_VIRTUALIZATIONS = {"docker", "podman", "rkt", "oci"}
_ACTIVE_MACHINE_TYPE: ContextVar[Optional[str]] = ContextVar(
    "infra_tools_active_machine_type",
    default=None,
)


def _systemd_detect_virt() -> Optional[str]:
    """Return the virtualization identifier reported by systemd, if any."""
    detector = shutil.which("systemd-detect-virt")
    if not detector:
        return None

    try:
        result = subprocess.run(
            [detector],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    if result.returncode != 0:
        return None
    value = result.stdout.strip().lower()
    return value or None


def _read_text(paths: tuple[str, ...]) -> Optional[str]:
    for path in paths:
        try:
            with open(path, "r", encoding="utf-8") as file_obj:
                value = file_obj.read().strip().lower()
        except OSError:
            continue
        if value:
            return value
    return None


def detect_machine_type() -> str:
    """Guess the supported machine profile from the local runtime environment."""
    virtualization = _systemd_detect_virt()
    if virtualization in _LXC_VIRTUALIZATIONS:
        return "unprivileged"
    if virtualization in _OCI_VIRTUALIZATIONS:
        return "oci"
    if virtualization and virtualization != "none":
        return "vm"

    container_marker = _read_text(("/run/systemd/container",))
    if container_marker in _LXC_VIRTUALIZATIONS:
        return "unprivileged"
    if container_marker:
        return "oci"

    cgroup = _read_text(("/proc/1/cgroup",)) or ""
    if any(marker in cgroup for marker in _LXC_VIRTUALIZATIONS):
        return "unprivileged"
    if any(marker in cgroup for marker in _OCI_VIRTUALIZATIONS):
        return "oci"

    if os.path.exists("/.dockerenv") or os.path.exists("/run/.containerenv"):
        return "oci"

    product_name = _read_text(("/sys/class/dmi/id/product_name",)) or ""
    if any(
        marker in product_name
        for marker in ("qemu", "kvm", "vmware", "virtualbox", "microsoft", "xen", "virtual")
    ):
        return "vm"

    cpuinfo = _read_text(("/proc/cpuinfo",)) or ""
    if "hypervisor" in cpuinfo:
        return "vm"

    return "hardware"


def resolve_machine_type(machine_type: Optional[str]) -> str:
    """Resolve ``auto`` to the machine profile detected on this host."""
    if machine_type in (None, AUTO_MACHINE_TYPE):
        return detect_machine_type()
    return machine_type


@contextmanager
def machine_type_context(machine_type: Optional[str]) -> Iterator[None]:
    """Temporarily expose the current setup's resolved machine type.

    Setup state is committed only after every mutation succeeds. Capability
    helpers still need the current run's type while those mutations execute,
    rather than the type saved by a previous successful run.
    """
    resolved_type = resolve_machine_type(machine_type)
    if resolved_type == AUTO_MACHINE_TYPE or resolved_type not in MACHINE_TYPES:
        raise ValueError(f"Unknown machine type: {resolved_type!r}")

    token = _ACTIVE_MACHINE_TYPE.set(resolved_type)
    try:
        yield
    finally:
        _ACTIVE_MACHINE_TYPE.reset(token)


def save_machine_state(
    machine_type: str,
    system_type: str,
    username: str,
    extra_data: Optional[dict[str, Any]] = None
) -> None:
    """Save machine state to the target system."""
    load_machine_state()
    os.makedirs(STATE_DIR, exist_ok=True)
    
    state: dict[str, Any] = {
        "machine_type": machine_type,
        "system_type": system_type,
        "username": username,
    }
    
    if extra_data:
        state.update(extra_data)
    
    write_json_atomic(STATE_FILE, state)


def _default_machine_state() -> dict[str, Any]:
    """Return default state only when the state file is missing."""
    return {
        "machine_type": DEFAULT_MACHINE_TYPE,
        "system_type": None,
        "username": None,
    }


def _validate_machine_state(state: Any) -> Optional[str]:
    """Validate machine state structure.

    Returns None if valid, or an error message string if invalid.
    """
    if not isinstance(state, dict):
        return f"Expected dict, got {type(state).__name__}"

    missing = [k for k in _MACHINE_STATE_REQUIRED_KEYS if k not in state]
    if missing:
        return f"Missing required keys: {', '.join(missing)}"

    machine_type = state["machine_type"]
    if machine_type is not None and (not isinstance(machine_type, str) or machine_type not in MACHINE_TYPES):
        return f"Unknown machine_type: {machine_type!r}"

    for field in ("system_type", "username"):
        if state[field] is not None and not isinstance(state[field], str):
            return f"Invalid {field} type"
    if state["username"] is not None and not validate_username(state["username"]):
        return "Invalid username"

    return None


def load_machine_state() -> dict[str, Any]:
    """Load machine state from the target system."""
    state = read_state_object(STATE_FILE)
    if state is None:
        return _default_machine_state()

    error = _validate_machine_state(state)
    if error:
        raise StateReadError(STATE_FILE, "invalid machine state fields")

    return state


def get_machine_type() -> str:
    """Get the active setup type, falling back to stored machine state."""
    active_type = _ACTIVE_MACHINE_TYPE.get()
    if active_type is not None:
        return active_type

    state = load_machine_state()
    return resolve_machine_type(state.get("machine_type", DEFAULT_MACHINE_TYPE))


def is_unprivileged() -> bool:
    """Check if running in an unprivileged LXC container."""
    return get_machine_type() == "unprivileged"


def is_oci() -> bool:
    """Check if running in an OCI container (Docker, Podman)."""
    return get_machine_type() == "oci"


def is_container() -> bool:
    """Check if running in any container type."""
    return get_machine_type() in ("unprivileged", "oci")


def is_vm() -> bool:
    """Check if running in a virtual machine."""
    return get_machine_type() == "vm"


def is_privileged_container() -> bool:
    """Check if running in a privileged container."""
    return get_machine_type() == "privileged"


def is_hardware() -> bool:
    """Check if running on bare metal hardware."""
    return get_machine_type() == "hardware"


def can_modify_kernel() -> bool:
    """Check if kernel parameters can be modified."""
    return get_machine_type() in ("vm", "privileged", "hardware")


def _effective_machine_type(machine_type: Optional[str]) -> str:
    """Return an explicit setup type or the active/persisted runtime type."""
    return (
        get_machine_type()
        if machine_type is None
        else resolve_machine_type(machine_type)
    )


def can_manage_firewall(machine_type: Optional[str] = None) -> bool:
    """Check if firewall policy can be enforced by this machine."""
    return _effective_machine_type(machine_type) in (
        "vm",
        "privileged",
        "hardware",
    )


def can_manage_swap() -> bool:
    """Check if swap can be configured."""
    return get_machine_type() in ("vm", "privileged", "hardware")


def can_manage_time_sync(machine_type: Optional[str] = None) -> bool:
    """Check if time synchronization can be configured.

    Setup steps may pass the currently resolved machine type so their
    capability decision does not depend on state saved by an older run.
    Runtime callers continue to use the persisted machine state.
    """
    effective_type = _effective_machine_type(machine_type)
    return effective_type in ("vm", "privileged", "hardware")


def can_manage_mdns() -> bool:
    """Check if a target can run a system mDNS daemon."""
    return get_machine_type() != "oci"


def can_manage_system_services(machine_type: Optional[str] = None) -> bool:
    """Check if a target can run persistent systemd services."""
    return _effective_machine_type(machine_type) != "oci"


def can_restart_system() -> bool:
    """Check if system restart is possible.
    
    LXC containers can restart themselves. OCI containers cannot.
    """
    return get_machine_type() != "oci"


def save_setup_config(config_dict: dict[str, Any]) -> None:
    """Save the setup configuration to the target system for later recall."""
    load_setup_config()
    os.makedirs(STATE_DIR, exist_ok=True)

    sanitized_config = dict(config_dict)
    sanitized_config.pop("password", None)
    write_json_atomic(SETUP_CONFIG_FILE, sanitized_config)

    username = sanitized_config.get("username")
    notification_group = -1
    if isinstance(username, str):
        try:
            notification_group = pwd.getpwnam(username).pw_gid
        except KeyError:
            # Unit tests and partially provisioned targets may not have the
            # account yet.  A root-only file is safer than guessing a group;
            # the next successful setup will reconcile its ownership.
            pass

    notification_state = {
        "version": 1,
        "notify_specs": sanitized_config.get("notify_specs") or [],
        "notification_level": sanitized_config.get("notification_level"),
    }
    write_json_atomic(
        NOTIFICATION_CONFIG_FILE,
        notification_state,
        mode=0o640 if notification_group != -1 else 0o600,
        gid=notification_group,
    )


def _validate_setup_config(config: Any) -> Optional[str]:
    """Validate setup config structure.

    Returns None if valid, or an error message string if invalid.
    """
    if not isinstance(config, dict):
        return f"Expected dict, got {type(config).__name__}"

    missing = [k for k in _SETUP_CONFIG_REQUIRED_KEYS if k not in config]
    if missing:
        return f"Missing required keys: {', '.join(missing)}"

    system_type = config.get("system_type")
    if system_type is not None and (not isinstance(system_type, str) or system_type not in get_system_type_names()):
        return f"Unknown system_type: {system_type!r}"

    machine_type = config.get("machine_type")
    if machine_type is not None and (not isinstance(machine_type, str) or machine_type not in MACHINE_TYPES):
        return f"Unknown machine_type: {machine_type!r}"

    username = config.get("username")
    if username is not None and (not isinstance(username, str) or not validate_username(username)):
        return "Invalid username"

    return None


def load_setup_config() -> Optional[dict[str, Any]]:
    """Load the setup configuration from the target system."""
    config = read_state_object(SETUP_CONFIG_FILE)
    if config is None:
        return None

    error = _validate_setup_config(config)
    if error:
        raise StateReadError(SETUP_CONFIG_FILE, "invalid setup configuration fields")

    if isinstance(config, dict) and "password" in config:
        del config["password"]
        try:
            write_json_atomic(SETUP_CONFIG_FILE, config)
        except OSError as exc:
            print(f"Warning: Failed to remove password from saved setup configuration: {exc}")

    return config


def load_notification_state() -> Optional[dict[str, Any]]:
    """Load the least-privileged notification state used by service jobs."""

    return read_state_object(NOTIFICATION_CONFIG_FILE)
