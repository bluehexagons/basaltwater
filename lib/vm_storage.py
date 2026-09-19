"""Shared models and stable identities for provisioned VM data storage."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lib.config import SetupConfig


_UNIT_TO_KIB = {
    "K": 1,
    "M": 1024,
    "G": 1024 * 1024,
    "T": 1024 * 1024 * 1024,
}


@dataclass(frozen=True)
class VMDataDisk:
    """A validated, provider-backed non-root VM disk declaration."""

    name: str
    pool: str
    size: str

    @property
    def serial(self) -> str:
        return storage_disk_serial(self.name)


@dataclass(frozen=True)
class VMDiskHardware:
    """Effective Proxmox hardware hints for one logical VM disk."""

    name: str
    discard: bool
    ssd: bool
    backup: bool = True


@dataclass(frozen=True)
class VMStorageMount:
    """A validated guest mount declaration for one named VM data disk."""

    name: str
    path: str
    filesystem: str = "ext4"
    policy: str = "empty"


@dataclass(frozen=True)
class VMStorageCache:
    """A validated LVM cache relationship between two named VM disks."""

    data_name: str
    cache_name: str
    mode: str = "writethrough"


def storage_size_kib(value: str) -> int:
    """Convert a validated binary-size declaration to KiB."""

    return int(value[:-1]) * _UNIT_TO_KIB[value[-1].upper()]


def storage_disk_serial(name: str) -> str:
    """Return the stable serial reported by Proxmox to the guest.

    Proxmox limits drive serials to 20 bytes. Validation limits logical names
    so the ``bw-`` prefix plus the complete name always fits without a lossy
    truncation or hash collision.
    """

    return f"bw-{name}"


def storage_disk_serials(name: str) -> tuple[str, str]:
    """Recognize current and retained recent-install identities, never relabel."""

    return storage_disk_serial(name), f"it-{name}"


def data_disks(config: SetupConfig) -> list[VMDataDisk]:
    """Return non-root, non-template storage declarations in CLI order."""

    result: list[VMDataDisk] = []
    for spec in config.container_storage or []:
        if len(spec) == 3 and spec[0] not in {"root", "template"}:
            result.append(VMDataDisk(spec[0], spec[1], spec[2]))
    return result


def disk_hardware(config: SetupConfig) -> dict[str, VMDiskHardware]:
    """Return VM-wide disk defaults merged with per-device overrides."""

    default_discard = bool(getattr(config, "vm_disk_discard", True))
    default_ssd = bool(getattr(config, "vm_disk_ssd", False))
    default_backup = bool(getattr(config, "vm_disk_backup", True))
    settings = {
        name: VMDiskHardware(name, default_discard, default_ssd, default_backup)
        for name in ["root", *(disk.name for disk in data_disks(config))]
    }
    for spec in getattr(config, "vm_disk_settings", None) or []:
        if len(spec) < 2 or spec[0] not in settings:
            continue
        current = settings[spec[0]]
        discard = current.discard
        ssd = current.ssd
        backup = current.backup
        for option in spec[1:]:
            setting, separator, enabled = option.partition("=")
            if not separator or enabled not in {"on", "off"}:
                continue
            if setting == "discard":
                discard = enabled == "on"
            elif setting == "ssd":
                ssd = enabled == "on"
            elif setting == "backup":
                backup = enabled == "on"
        settings[spec[0]] = VMDiskHardware(spec[0], discard, ssd, backup)

    # Swap contains no durable guest data. Excluding it also prevents restore
    # jobs from spending time and backup space on an unusable memory snapshot.
    from lib.swap_config import swap_device_disk_names

    for name in swap_device_disk_names(config):
        current = settings[name]
        settings[name] = VMDiskHardware(name, current.discard, current.ssd, False)
    return settings


def storage_mounts(config: SetupConfig) -> list[VMStorageMount]:
    """Return normalized mount declarations in CLI order."""

    result: list[VMStorageMount] = []
    for spec in config.storage_mounts or []:
        if len(spec) < 2:
            continue
        result.append(
            VMStorageMount(
                name=spec[0],
                path=spec[1],
                filesystem=spec[2] if len(spec) >= 3 else "ext4",
                policy=spec[3] if len(spec) >= 4 else "empty",
            )
        )
    return result


def storage_caches(config: SetupConfig) -> list[VMStorageCache]:
    """Return normalized VM block-cache declarations in CLI order."""

    result: list[VMStorageCache] = []
    for spec in config.storage_caches or []:
        if len(spec) < 2:
            continue
        result.append(
            VMStorageCache(
                data_name=spec[0],
                cache_name=spec[1],
                mode=spec[2] if len(spec) >= 3 else "writethrough",
            )
        )
    return result


def has_home_mount(config: SetupConfig) -> bool:
    """Return whether provisioning will mount a dedicated filesystem at /home."""

    return any(mount.path == "/home" for mount in storage_mounts(config))
