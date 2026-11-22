"""Opt-in native SSH for a CachyOS desktop; no desktop session is created."""

from __future__ import annotations

from pathlib import Path

from lib.config import SetupConfig
from lib.machine_state import can_manage_system_services
from lib.remote_utils import is_dry_run, run
from lib.runtime_source import require_runtime_source
from lib.validators import validate_username

SERVICE = "basaltwater-cachyos-ssh.service"
BOOTSTRAP_SERVICE = "basaltwater-cachyos-ssh-bootstrap.service"
SSH_PORT = 22
BOOTSTRAP_PORT = 2222
ADMIN_SOURCE = Path("/opt/basaltwater-cachyos-ssh")
ADMIN_HELPER = ADMIN_SOURCE / "common/service_tools/cachyos_ssh.py"


def _check_admin_installation() -> None:
    try:
        require_runtime_source(ADMIN_SOURCE, owner_uid=0)
        if not ADMIN_HELPER.is_file() or not all(
            (ADMIN_SOURCE / package / "__init__.py").is_file()
            for package in ("common", "lib", "plugins")
        ):
            raise ValueError("Incomplete SSH lifecycle helper installation")
    except (OSError, ValueError) as exc:
        raise ValueError(
            "Install a verified Basaltwater SSH helper as administrator at "
            f"{ADMIN_SOURCE} before changing SSH; see the CachyOS workstation guide"
        ) from exc


def _options(config: SetupConfig) -> tuple[str, int]:
    selected = getattr(config, "install_ssh", None)
    password = getattr(config, "ssh_password_auth", False)
    window = getattr(config, "ssh_password_window", None)
    if selected is not None and type(selected) is not bool:
        raise ValueError("install_ssh must be a boolean or omitted")
    if type(password) is not bool or (window is not None and
            (type(window) is not int or not 1 <= window <= 60)):
        raise ValueError("SSH password window must be 1..60 minutes")
    if password or window is not None:
        if selected is not True or (password and window is not None):
            raise ValueError("SSH authentication options require SSH and are mutually exclusive")
        from common.cachyos_firewall import validate_sources

        sources = validate_sources(config.access_sources or [])
        if not (config.lan_access is True or sources):
            raise ValueError("Password SSH requires explicit private access selection")
    if not validate_username(config.username) or config.username == "root":
        raise ValueError("SSH requires an existing non-root desktop username")
    mode = ("leave" if selected is None else "off" if selected is False
            else "password" if password else "key")
    return mode, window or 0


def preflight(config: SetupConfig) -> bool:
    """Read-only, unprivileged checks before packages/firewall/setup mutations.

    Call after resolving install_ssh defaults. The root helper repeats these
    checks under its lock; this probe neither authorizes nor changes a service.
    Dry runs validate arguments only. Hidden listener PIDs are checked by root.
    Return whether this selection requires the installed helper.
    """
    mode, minutes = _options(config)
    if config.dry_run or is_dry_run():
        return False
    if not can_manage_system_services(config.machine_type):
        raise ValueError("This machine cannot manage system SSH services")
    from common.service_tools.cachyos_ssh import inspect_setup

    _, owned, native, bootstrap = inspect_setup(config.username, mode, minutes, metadata_only=True)
    needed = mode in {"key", "password"} or bootstrap or owned or any(
        state["ActiveState"] == "active" or state["UnitFileState"] in {"enabled", "enabled-runtime"}
        for state in native.values()
    )
    if needed:
        _check_admin_installation()
    return needed


def configure(config: SetupConfig) -> None:
    """Authenticate once and run the privileged, one-shot lifecycle helper."""
    mode, minutes = _options(config)
    if config.dry_run or is_dry_run():
        print(f"  Would configure native SSH: {mode}; password bootstrap minutes: {minutes}")
        return
    if not preflight(config):
        return
    environment = {"PATH": "/usr/bin", "LC_ALL": "C"}
    run(["/usr/bin/sudo", "-v"], interactive=True, timeout=120, cwd="/", env=environment)
    run(["/usr/bin/sudo", "-n", "--", "/usr/bin/python3", "-I", "-S", "-B",
         str(ADMIN_HELPER), config.username, mode, str(minutes)],
        interactive=True, timeout=180, cwd="/", env=environment)
