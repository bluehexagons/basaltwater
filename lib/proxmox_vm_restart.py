"""Verify and control Proxmox VM power after a successful guest setup."""

from __future__ import annotations

import ipaddress
import json
import re
import shlex
from typing import Any

from lib.config import SetupConfig
from lib.proxmox_guest import _build_guest_hostname
from lib.remote_utils import run
from lib.ssh_utils import build_ssh_command, get_ssh_control_path, ssh_batch_mode
from lib.validation import validate_filesystem_path, validate_positive_integer
from lib.validators import validate_host, validate_username


def _provider_command(config: SetupConfig, command: str, *, timeout: int = 30) -> str:
    if not config.hosted_node or not validate_host(config.hosted_node):
        raise ValueError("Invalid Proxmox power operation host")
    if not validate_username(config.hosted_user):
        raise ValueError("Invalid Proxmox power operation username")
    if config.hosted_key:
        validate_filesystem_path(config.hosted_key, must_exist=False)
    result = run(
        build_ssh_command(
            config.hosted_node, config.hosted_user, config.hosted_key,
            remote_command=command, batch_mode=ssh_batch_mode(), connect_timeout=15,
            server_alive_interval=15,
            control_path=get_ssh_control_path(config.hosted_node, config.hosted_user, config.hosted_key),
        ),
        capture_output=True, text=True, check=False, timeout=timeout,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip() or "SSH command failed"
        raise RuntimeError(f"Proxmox power operation on {config.hosted_node}: {detail}")
    return result.stdout


def _api(config: SetupConfig, path: str) -> Any:
    return json.loads(_provider_command(
        config, shlex.join(["pvesh", "get", path, "--output-format", "json"]),
    ))


def _vmid(value: object) -> int:
    if type(value) is not int:
        raise ValueError("Invalid Proxmox restart VMID")
    vmid = validate_positive_integer(str(value), "VMID")
    if not 100 <= vmid <= 999999999:
        raise ValueError("Invalid Proxmox restart VMID")
    return vmid


def _verified_vm(config: SetupConfig) -> tuple[int, dict[str, Any], dict[str, Any]]:
    """Match both the saved name and setup address before a power operation."""
    expected_name = config.system_hostname or _build_guest_hostname(
        config.host, config.friendly_name, default_prefix="vm",
    )
    guests = _api(config, "/nodes/localhost/qemu")
    if not isinstance(guests, list) or not all(
        isinstance(guest, dict) and isinstance(guest.get("name", ""), str)
        for guest in guests
    ):
        raise ValueError("Invalid Proxmox VM inventory")
    matches = [guest for guest in guests if guest.get("name", "").lower() == expected_name.lower()]
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one Proxmox VM named {expected_name!r}")
    guest = matches[0]
    vmid = _vmid(guest.get("vmid"))
    path = f"/nodes/localhost/qemu/{vmid}"
    observed = _api(config, path + "/config")
    if not isinstance(observed, dict):
        raise ValueError("Invalid Proxmox VM configuration")
    addresses = set()
    for key, value in observed.items():
        if re.fullmatch(r"ipconfig\d+", key) and isinstance(value, str):
            for option in value.split(","):
                if option.startswith("ip=") and option != "ip=dhcp":
                    addresses.add(str(ipaddress.ip_interface(option[3:]).ip))
    if config.host not in addresses:
        raise ValueError(f"Proxmox VM {vmid} does not have the setup address {config.host}")
    return vmid, guest, observed


def pending_vm_restart(config: SetupConfig) -> int | None:
    """Return a verified VMID only when Proxmox has unapplied changes."""
    vmid, guest, observed = _verified_vm(config)
    path = f"/nodes/localhost/qemu/{vmid}"

    # Unlike qm pending's text display, the API explicitly separates current
    # values from pending replacements and removals.
    rows = _api(config, path + "/pending")
    if not isinstance(rows, list) or not all(
        isinstance(row, dict) and isinstance(row.get("key"), str)
        and re.fullmatch(r"[a-z][a-z0-9_-]*", row["key"])
        and type(row.get("delete", 0)) in {int, bool} and row.get("delete", 0) in {0, 1, 2}
        and ("pending" not in row or type(row["pending"]) in {str, int, float, bool})
        and ("value" in row or "pending" in row or row.get("delete"))
        for row in rows
    ):
        raise ValueError("Invalid Proxmox pending configuration")
    pending = sorted(row["key"] for row in rows if "pending" in row or row.get("delete"))
    if not pending:
        return None
    if guest.get("status") != "running" or guest.get("lock") or observed.get("lock"):
        raise ValueError(f"Proxmox VM {vmid} is not running or is locked")
    print(f"  Proxmox VM {vmid} has pending configuration: {', '.join(pending)}")
    return vmid


def request_vm_restart(config: SetupConfig, vmid: int) -> None:
    """Gracefully restart through Proxmox so QEMU applies pending settings."""
    vmid = _vmid(vmid)
    _provider_command(config, f"qm reboot {vmid} --timeout 180", timeout=240)


def request_vm_shutdown(config: SetupConfig) -> None:
    """Gracefully shut down the verified VM and confirm it stopped."""
    vmid, guest, observed = _verified_vm(config)
    if guest.get("lock") or observed.get("lock"):
        raise ValueError(f"Proxmox VM {vmid} is locked")
    if guest.get("status") == "stopped":
        return
    if guest.get("status") != "running":
        raise ValueError(f"Proxmox VM {vmid} is not running")
    _provider_command(
        config, f"qm shutdown {vmid} --timeout 180 --forceStop 0", timeout=240,
    )
    status = _api(config, f"/nodes/localhost/qemu/{vmid}/status/current")
    if not isinstance(status, dict) or status.get("status") != "stopped":
        raise RuntimeError(f"Proxmox VM {vmid} did not shut down")
