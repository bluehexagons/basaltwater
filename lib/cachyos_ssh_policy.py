"""Resolve fresh CachyOS SSH defaults using read-only target observations."""

from __future__ import annotations

import ipaddress
import os
import re
import subprocess

from lib.config import SetupConfig
from lib.machine_state import can_manage_system_services
from lib.validators import validate_ip_address


_UNITS = ("sshd.service", "sshd.socket", "basaltwater-cachyos-ssh.service")
_UNKNOWN = (
    "Cannot determine the current CachyOS SSH state. "
    "Choose --ssh or --no-ssh explicitly, then retry."
)


def _ssh_session() -> bool:
    """Accept well-formed OpenSSH session markers without printing their values."""
    connection = os.environ.get("SSH_CONNECTION", "").split()

    def valid_address(value: str) -> bool:
        if ":" not in value:
            return validate_ip_address(value)
        try:
            ipaddress.IPv6Address(value)
        except ValueError:
            return False
        return True

    if len(connection) == 4 and all(
        valid_address(connection[index]) for index in (0, 2)
    ) and all(
        re.fullmatch(r"[0-9]{1,5}", connection[index])
        and 1 <= int(connection[index]) <= 65535 for index in (1, 3)
    ):
        return True
    return re.fullmatch(r"/dev/(?:pts/[0-9]+|tty[0-9]+)",
                        os.environ.get("SSH_TTY", "")) is not None


def detect_ssh_enabled(config: SetupConfig) -> bool | None:
    """Return active/enabled SSH, confirmed off, or unknown; never mutate units."""
    if _ssh_session():
        return True
    if not can_manage_system_services(config.machine_type):
        return None
    unknown = False
    for unit in _UNITS:
        try:
            # Bypass the mutation runner's dry-run suppression: these fixed,
            # bounded probes are read-only and their output stays private.
            result = subprocess.run(
                ["systemctl", "show", unit, "--no-pager",
                 "--property=LoadState,ActiveState,UnitFileState"],
                capture_output=True, text=True, check=False, timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            unknown = True
            continue
        properties = dict(line.split("=", 1) for line in result.stdout.splitlines()
                          if "=" in line)
        if (len(result.stdout.splitlines()) != 3
                or set(properties) != {"LoadState", "ActiveState", "UnitFileState"}):
            unknown = True
            continue
        load = properties.get("LoadState")
        active = properties.get("ActiveState")
        enabled = properties.get("UnitFileState")
        if result.returncode not in (0, 4) or (result.returncode == 4 and load != "not-found"):
            unknown = True
        elif load in {"loaded", "masked"} and (
            active == "active" or enabled in {"enabled", "enabled-runtime"}
        ):
            return True
        elif load == "not-found" and active == "inactive" and enabled in {"", "not-found"}:
            continue
        elif (load in {"loaded", "masked"} and active in {"inactive", "failed"}
              and enabled in {"disabled", "static", "indirect", "masked", "masked-runtime",
                              "linked", "linked-runtime", "generated", "transient"}):
            continue
        else:
            unknown = True
    return None if unknown else False


def resolve_ssh_policy(config: SetupConfig, *, target_is_cachyos: bool) -> None:
    """Resolve only fresh runtime defaults and select private LAN discovery."""
    if config.install_ssh is None:
        if getattr(config, "_cachyos_remote_setup", False):
            config.install_ssh = True
        elif target_is_cachyos:
            config.install_ssh = detect_ssh_enabled(config)
        if config.install_ssh is None:
            if config.dry_run:
                print("[DRY-RUN] SSH default deferred: apply will detect the target's current "
                      "SSH state; enabled SSH selects private LAN access unless explicitly set. "
                      "Use --ssh or --no-ssh to select a policy now.")
                return
            if target_is_cachyos:
                raise ValueError(_UNKNOWN) from None
            return  # The platform preflight reports unsupported apply hosts.
    if config.install_ssh and not any((config.lan_access, config.access_sources,
                                       config.clear_lan_access, config.clear_access_sources)):
        config.lan_access = True
    if config.dry_run:
        network = "private LAN discovery" if config.lan_access else "explicit/local-only access policy"
        print(f"[DRY-RUN] SSH policy: {'enabled' if config.install_ssh else 'disabled'}; {network}")


def check_ssh_source(config: SetupConfig) -> None:
    """Refuse a firewall handover that would exclude the current SSH client."""
    if config.dry_run or config.install_ssh is not True:
        return
    connection = os.environ.get("SSH_CONNECTION", "").split()
    if not _ssh_session() or len(connection) != 4:
        return
    from common.cachyos_firewall import access_sources, firewall_requested

    if not firewall_requested(config):
        return
    try:
        client = ipaddress.ip_address(connection[0])
    except ValueError:
        raise ValueError("Cannot validate the current SSH client; use a local terminal.") from None
    if client.version != 4 or not any(
        client in ipaddress.ip_network(source) for source in access_sources(config)
    ):
        raise ValueError("Current SSH client is outside the selected private IPv4 access sources. "
                         "Connect over the private LAN and select a matching --access-source before applying SSH setup.")
