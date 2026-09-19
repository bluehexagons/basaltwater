"""Host-level Proxmox setup steps."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from lib.proxmox_memory import (
    DEFAULT_BALLOON_TARGET_PERCENT,
    SWAPON_STATUS_COMMAND,
    calculate_balloon_target,
    format_gib,
    parse_swapon_output,
)
from lib.remote_utils import is_dry_run, run

if TYPE_CHECKING:
    from lib.config import SetupConfig


_PROXMOX_MEMORY_SYSCTL_FILE = (
    "/etc/sysctl.d/99-zz-basaltwater-proxmox-memory.conf"
)
_PROXMOX_SWAPPINESS = 10
_APPLY_SWAPPINESS_COMMAND = (
    "/usr/lib/systemd/systemd-sysctl --prefix=/vm/swappiness"
)
_NODE_CONFIG_COMMAND = (
    "pvesh get /nodes/$(hostname -s)/config --output-format json"
)


def configure_proxmox_host_memory_safety(config: SetupConfig) -> None:
    """Audit host swap topology and apply Proxmox's low swappiness policy."""
    del config
    sysctl_content = (
        "# Managed by basaltwater for Proxmox hosts.\n"
        f"vm.swappiness = {_PROXMOX_SWAPPINESS}\n"
    )

    if is_dry_run():
        run(SWAPON_STATUS_COMMAND, check=False, capture_output=True)
        run("install -d -m 0755 /etc/sysctl.d")
        run(
            f"tee {_PROXMOX_MEMORY_SYSCTL_FILE}",
            capture_output=True,
            input_data=sysctl_content,
        )
        run(f"chmod 0644 {_PROXMOX_MEMORY_SYSCTL_FILE}")
        run(_APPLY_SWAPPINESS_COMMAND)
        return

    swap_result = run(SWAPON_STATUS_COMMAND, check=False, capture_output=True)
    if swap_result.returncode != 0:
        print("  ⚠ Could not inspect Proxmox host swap devices")
    else:
        devices = parse_swapon_output(swap_result.stdout or "")
        if not devices:
            print(
                "  ⚠ No host swap is active; memory spikes have no emergency "
                "reclaim cushion"
            )
        for device in devices:
            size = format_gib(device.size_bytes // (1024 * 1024))
            used = format_gib(device.used_bytes // (1024 * 1024))
            print(
                f"  Host swap: {device.name} ({device.device_type}), "
                f"{used} used of {size}"
            )
            if device.zfs_backed:
                print(
                    "  ⚠ Swap appears to be backed by a ZFS zvol; Proxmox "
                    "warns this can block the host under memory pressure"
                )

    current_result = run(
        "sysctl -n vm.swappiness",
        check=False,
        capture_output=True,
    )
    try:
        previous_swappiness = int((current_result.stdout or "").strip())
    except ValueError:
        previous_swappiness = None

    existing = run(
        f"cat {_PROXMOX_MEMORY_SYSCTL_FILE}",
        check=False,
        capture_output=True,
    )
    file_changed = existing.returncode != 0 or existing.stdout != sysctl_content
    if file_changed:
        run("install -d -m 0755 /etc/sysctl.d")
        run(
            f"tee {_PROXMOX_MEMORY_SYSCTL_FILE}",
            capture_output=True,
            input_data=sysctl_content,
        )
        run(f"chmod 0644 {_PROXMOX_MEMORY_SYSCTL_FILE}")

    if file_changed or previous_swappiness != _PROXMOX_SWAPPINESS:
        run(_APPLY_SWAPPINESS_COMMAND)

    verified = run(
        "sysctl -n vm.swappiness",
        capture_output=True,
    )
    try:
        verified_swappiness = int((verified.stdout or "").strip())
    except ValueError as exc:
        raise RuntimeError("Unable to verify Proxmox host swappiness") from exc
    if verified_swappiness != _PROXMOX_SWAPPINESS:
        raise RuntimeError(
            "Proxmox swappiness verification failed: "
            f"expected {_PROXMOX_SWAPPINESS}, found {verified_swappiness}"
        )
    if previous_swappiness == verified_swappiness:
        print(f"  ✓ Host swappiness already set to {verified_swappiness}")
    else:
        previous = (
            "unknown" if previous_swappiness is None else str(previous_swappiness)
        )
        print(f"  ✓ Changed host swappiness from {previous} to {verified_swappiness}")


def _host_total_memory_mib(meminfo_path: str = "/proc/meminfo") -> int:
    """Read physical host RAM from Linux meminfo."""
    try:
        with open(meminfo_path, "r", encoding="utf-8") as meminfo:
            for line in meminfo:
                if line.startswith("MemTotal:"):
                    fields = line.split()
                    return int(fields[1]) // 1024
    except (OSError, ValueError, IndexError) as exc:
        raise RuntimeError(f"Unable to determine Proxmox host memory: {exc}") from exc
    raise RuntimeError("Unable to determine Proxmox host memory: MemTotal is missing")


def _configured_balloon_target(output: str) -> int | None:
    """Extract an explicitly stored node target, if present."""
    if not output.strip():
        return None
    try:
        config: Any = json.loads(output)
        value = config.get("ballooning-target")
        if value is None:
            return None
        return int(value)
    except (AttributeError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            "Unable to read the existing Proxmox balloon target"
        ) from exc


def configure_proxmox_balloon_target(config: SetupConfig) -> None:
    """Apply an idempotent, host-size-aware Proxmox balloon target."""
    total_mib = _host_total_memory_mib()
    policy = calculate_balloon_target(
        total_mib,
        getattr(config, "proxmox_balloon_target", None),
    )
    source = "automatic" if policy.automatic else "override"
    print(
        "  Proxmox memory policy: "
        f"{format_gib(policy.total_mib)} host RAM, "
        f"{policy.target_percent}% balloon target ({source}), "
        f"{format_gib(policy.reserve_mib)} host headroom"
    )
    if policy.automatic and total_mib < 4096:
        print(
            "  ⚠ Host RAM is below 4 GiB; the 50% safety floor cannot preserve "
            "a full 2 GiB of headroom"
        )

    set_command = (
        "pvenode config set "
        f"--ballooning-target {policy.target_percent}"
    )
    if is_dry_run():
        run(set_command)
        return

    current = run(_NODE_CONFIG_COMMAND, capture_output=True)
    current_target = _configured_balloon_target(current.stdout or "")
    if current_target == policy.target_percent:
        print(f"  ✓ Balloon target already set to {current_target}%")
        return

    run(set_command)
    verified = run(_NODE_CONFIG_COMMAND, capture_output=True)
    verified_target = _configured_balloon_target(verified.stdout or "")
    if verified_target != policy.target_percent:
        raise RuntimeError(
            "Proxmox balloon target verification failed: "
            f"expected {policy.target_percent}%, found {verified_target}%"
        )
    previous = (
        f"{current_target}%"
        if current_target is not None
        else f"implicit default ({DEFAULT_BALLOON_TARGET_PERCENT}%)"
    )
    print(f"  ✓ Changed balloon target from {previous} to {verified_target}%")
