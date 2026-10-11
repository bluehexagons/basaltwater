"""Shared Proxmox memory policy and capacity helpers."""

from __future__ import annotations

from dataclasses import dataclass

from lib.validation import validate_positive_integer


DEFAULT_BALLOON_TARGET_PERCENT = 80
MAX_AUTOMATIC_BALLOON_TARGET_PERCENT = 80
MIN_AUTOMATIC_BALLOON_TARGET_PERCENT = 50
MIN_HOST_RESERVE_MIB = 2048
DEFAULT_GUEST_MEMORY_MIB = 512
SWAPON_STATUS_COMMAND = (
    "swapon --show=NAME,TYPE,SIZE,USED --bytes --noheadings --raw"
)


@dataclass(frozen=True)
class BalloonTargetPolicy:
    """Calculated Proxmox host memory target and its reserved headroom."""

    total_mib: int
    target_percent: int
    reserve_mib: int
    automatic: bool


@dataclass(frozen=True)
class GuestMemoryAllocation:
    """Memory floor and burst ceiling for one running Proxmox guest."""

    guest_type: str
    vmid: int
    minimum_mib: int
    maximum_mib: int


@dataclass(frozen=True)
class HostSwapDevice:
    """One active host swap device from stable ``swapon`` columns."""

    name: str
    device_type: str
    size_bytes: int
    used_bytes: int

    @property
    def zfs_backed(self) -> bool:
        """Return whether the path directly identifies a ZFS zvol."""
        direct_zvol = self.name.startswith("/dev/zvol/")
        zvol_block_device = (
            self.name.startswith("/dev/zd") and self.name[7:].isdigit()
        )
        return direct_zvol or zvol_block_device


def calculate_balloon_target(
    total_mib: int,
    override_percent: int | None = None,
) -> BalloonTargetPolicy:
    """Return a host target that preserves 20% or 2 GiB, whichever is larger."""
    if total_mib <= 0:
        raise ValueError("Host memory must be greater than zero")

    if override_percent is None:
        desired_reserve_mib = max(MIN_HOST_RESERVE_MIB, total_mib // 5)
        usable_mib = max(0, total_mib - desired_reserve_mib)
        target_percent = (usable_mib * 100) // total_mib
        target_percent = max(
            MIN_AUTOMATIC_BALLOON_TARGET_PERCENT,
            min(MAX_AUTOMATIC_BALLOON_TARGET_PERCENT, target_percent),
        )
        automatic = True
    else:
        target_percent = override_percent
        automatic = False

    reserve_mib = total_mib - ((total_mib * target_percent) // 100)
    return BalloonTargetPolicy(
        total_mib=total_mib,
        target_percent=target_percent,
        reserve_mib=reserve_mib,
        automatic=automatic,
    )


def parse_guest_memory_mib(value: str, *, guest_type: str) -> int:
    """Read Proxmox's integer or QEMU ``current=`` memory property in MiB."""
    if guest_type not in {"qemu", "lxc"}:
        raise ValueError(f"Unknown Proxmox guest type: {guest_type}")
    if guest_type == "qemu":
        value = value.removeprefix("current=")
    if not value.isascii() or not value.isdecimal():
        raise ValueError(f"Invalid {guest_type} memory value: {value!r}")
    return validate_positive_integer(value, f"{guest_type} memory")


def parse_swapon_output(output: str) -> list[HostSwapDevice]:
    """Parse ``NAME,TYPE,SIZE,USED`` output produced with ``--bytes``."""
    devices: list[HostSwapDevice] = []
    for line in output.splitlines():
        fields = line.split()
        if len(fields) < 4:
            continue
        try:
            size_bytes = int(fields[2])
            used_bytes = int(fields[3])
        except ValueError:
            continue
        devices.append(
            HostSwapDevice(
                name=fields[0],
                device_type=fields[1],
                size_bytes=size_bytes,
                used_bytes=used_bytes,
            )
        )
    return devices


def parse_guest_memory_config(
    config_text: str,
    *,
    guest_type: str,
    vmid: int,
) -> GuestMemoryAllocation:
    """Parse a running QEMU or LXC configuration into floor/ceiling values."""
    values: dict[str, str] = {}
    for raw_line in config_text.splitlines():
        key, separator, value = raw_line.partition(":")
        if separator:
            values[key.strip()] = value.strip()

    if not values:
        raise ValueError("Proxmox returned an empty or invalid guest configuration")
    maximum_mib = parse_guest_memory_mib(
        values.get("memory", str(DEFAULT_GUEST_MEMORY_MIB)),
        guest_type=guest_type,
    )

    if guest_type == "qemu":
        balloon = values.get("balloon", "0")
        if not balloon.isascii() or not balloon.isdecimal():
            raise ValueError(f"Invalid QEMU balloon value: {balloon!r}")
        balloon_mib = int(balloon)
        minimum_mib = (
            balloon_mib
            if 0 < balloon_mib < maximum_mib
            else maximum_mib
        )
    else:
        # LXC memory is a cgroup ceiling rather than a preallocated floor.
        minimum_mib = 0

    return GuestMemoryAllocation(
        guest_type=guest_type,
        vmid=vmid,
        minimum_mib=minimum_mib,
        maximum_mib=maximum_mib,
    )


def format_gib(memory_mib: int) -> str:
    """Format a MiB quantity as a compact GiB value."""
    return f"{memory_mib / 1024:.1f} GiB"
