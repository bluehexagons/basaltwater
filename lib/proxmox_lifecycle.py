"""Shared provisioning policy for QEMU and LXC guest lifecycles."""

from __future__ import annotations

import shlex
from subprocess import CompletedProcess
from typing import TYPE_CHECKING, Callable

from lib.proxmox_guest import ProvisionError
from lib.validation import validate_guest_lifecycle_settings

if TYPE_CHECKING:
    from lib.config import SetupConfig


LIFECYCLE_FIELD_FLAGS = {
    "guest_autostart": "--autostart",
    "guest_start_order": "--start-order",
    "guest_start_delay": "--start-delay",
    "guest_shutdown_timeout": "--shutdown-timeout",
    "guest_protection": "--protection",
}
_STARTUP_FIELDS = {
    "guest_start_order": "order",
    "guest_start_delay": "up",
    "guest_shutdown_timeout": "down",
}


def lifecycle_setup_args(config: SetupConfig) -> list[str]:
    """Reconstruct explicit choices, including false and zero values."""
    validate_guest_lifecycle_settings(config, require_provisioning=False)
    args: list[str] = []
    for field, flag in LIFECYCLE_FIELD_FLAGS.items():
        value = getattr(config, field)
        if value is not None:
            if type(value) is bool:
                args.append(flag if value else flag.replace("--", "--no-", 1))
            else:
                args.append(f"{flag} {value}")
    return args


def guest_startup(config: SetupConfig) -> str | None:
    """Return only declared members of Proxmox's startup property."""
    validate_guest_lifecycle_settings(config, require_provisioning=False)
    return ",".join(
        f"{key}={getattr(config, field)}"
        for field, key in _STARTUP_FIELDS.items()
        if getattr(config, field) is not None
    ) or None


def _parse_startup(value: str) -> dict[str, str]:
    """Preserve existing members, including Proxmox's implicit order syntax."""
    settings: dict[str, str] = {}
    for item in value.split(",") if value else []:
        key, separator, raw = item.strip().partition("=")
        if not separator and not settings:
            key, raw = "order", key
        if key not in {"order", "up", "down"} or key in settings or not (
            raw.isascii() and raw.isdecimal()
        ):
            raise ProvisionError("Cannot safely merge the guest's Proxmox startup setting")
        settings[key] = str(int(raw))
    return settings


def reconcile_guest_lifecycle(
    config: SetupConfig,
    *,
    tool: str,
    vmid: int,
    node_ip: str,
    user: str,
    ssh_opts: list[str],
    ssh_run: Callable[..., CompletedProcess[str]],
) -> None:
    """Apply and verify explicit policy after the caller verifies guest identity.

    New guests receive deletion protection only after bootstrap has succeeded,
    so failed VM creation can still use the ordinary cleanup path.
    """
    validate_guest_lifecycle_settings(config)
    options: dict[str, str] = {}
    for field, key in (("guest_autostart", "onboot"), ("guest_protection", "protection")):
        value = getattr(config, field)
        if value is not None:
            options[key] = str(int(value))
    startup = guest_startup(config)
    if not options and startup is None:
        return
    if tool not in {"qm", "pct"} or type(vmid) is not int or vmid < 100:
        raise ValueError("Invalid Proxmox guest lifecycle target")

    def read_config() -> dict[str, str]:
        result = ssh_run(node_ip, user, ssh_opts, f"{tool} config {vmid}", dry_run=False)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip() or "unknown error"
            raise ProvisionError(f"Could not inspect guest {vmid} lifecycle settings: {detail}")
        return {
            key.strip(): value.strip()
            for line in (result.stdout or "").splitlines()
            for key, separator, value in [line.partition(":")]
            if separator
        }

    current = read_config()
    if current.get("lock"):
        raise ProvisionError(f"Guest {vmid} is locked: {current['lock']}")
    if startup is not None:
        merged = _parse_startup(current.get("startup", ""))
        merged.update(_parse_startup(startup))
        options["startup"] = ",".join(f"{key}={value}" for key, value in merged.items())

    def matches(observed: dict[str, str], key: str, desired: str) -> bool:
        if key == "startup":
            return _parse_startup(observed.get(key, "")) == _parse_startup(desired)
        return observed.get(key, "0") == desired

    changes = {key: value for key, value in options.items() if not matches(current, key, value)}
    if not changes:
        return
    command = shlex.join([
        tool, "set", str(vmid),
        *[part for key, value in changes.items() for part in (f"--{key}", value)],
    ])
    result = ssh_run(node_ip, user, ssh_opts, command, dry_run=False)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip() or "unknown error"
        raise ProvisionError(f"Could not configure guest {vmid} lifecycle settings: {detail}")
    observed = read_config()
    if not all(matches(observed, key, value) for key, value in options.items()):
        raise ProvisionError(f"Proxmox did not preserve lifecycle settings for guest {vmid}")
    print(f"  ✓ Reconciled guest {vmid} startup and protection settings")


def print_guest_lifecycle(config: SetupConfig) -> None:
    """Describe initial defaults and existing-guest preservation in dry runs."""
    autostart = config.guest_autostart
    protection = config.guest_protection
    for label, value, default in (
        ("Auto-start at node boot", autostart, "enabled"),
        ("Deletion protection", protection, "disabled"),
    ):
        state = ("enabled" if value else "disabled") if value is not None else (
            f"{default} for new guests; preserve existing guests"
        )
        print(f"  {label}: {state}")
    if startup := guest_startup(config):
        print(f"  Startup settings: {startup} (preserve other existing values)")
