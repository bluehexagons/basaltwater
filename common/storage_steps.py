"""Fail-closed guest filesystem setup for managed VM data disks."""

from __future__ import annotations

import json
import os
import re
import shlex
import time
from typing import Any

from lib.atomic_io import write_json_atomic, write_text_atomic
from lib.config import SetupConfig
from lib.remote_utils import is_dry_run, run
from lib.validation import validate_vm_storage_name
from lib.vm_storage import (
    VMStorageCache,
    VMStorageMount,
    data_disks,
    storage_caches,
    storage_mounts,
    storage_size_kib,
    storage_disk_serials,
)


STORAGE_STATE_FILE = "/var/lib/basaltwater/vm-storage.json"
STORAGE_MARKER = ".basaltwater-storage.json"
STORAGE_SCHEMA_VERSION = 2


class _MissingDeclaredDisk(RuntimeError):
    """A managed hot-added disk is not visible in the guest yet."""


def _load_storage_state() -> dict[str, Any]:
    """Read retained identities; corrupt state must never authorize new storage."""

    _reject_symlinked_mount_path(STORAGE_STATE_FILE)
    try:
        with open(STORAGE_STATE_FILE, encoding="utf-8") as state_file:
            state = json.load(state_file)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise RuntimeError("Could not read managed VM storage state") from exc
    if not isinstance(state, dict) or type(state.get("schema_version")) is not int or state["schema_version"] not in {1, 2}:
        raise RuntimeError("Invalid managed VM storage schema")
    for key, identity in (("mounts", "name"), ("caches", "data_name")):
        records = state.get(key, [])
        if not isinstance(records, list):
            raise RuntimeError(f"Invalid managed VM storage {key}")
        seen: set[str] = set()
        for record in records:
            try:
                name = validate_vm_storage_name(record[identity])
            except (ValueError, TypeError, KeyError) as exc:
                raise RuntimeError(f"Invalid managed VM storage {key} identity") from exc
            if name in seen:
                raise RuntimeError(f"Duplicate managed VM storage identity: {name}")
            seen.add(name)
            if key == "mounts" and (
                record.get("serial") not in storage_disk_serials(name)
                or not isinstance(record.get("uuid"), str)
                or not re.fullmatch(r"[A-Fa-f0-9-]{8,64}", record["uuid"])
            ):
                raise RuntimeError(f"Invalid recorded VM storage identity: {name}")
    return state


def _run_capture(command: str, *, check: bool = True):
    return run(command, check=check, capture_output=True)


def _lsblk() -> list[dict[str, Any]]:
    result = _run_capture(
        "lsblk --json --bytes --paths "
        "--output NAME,PATH,TYPE,SIZE,SERIAL,FSTYPE,PTTYPE,MOUNTPOINTS,UUID"
    )
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError("lsblk returned invalid JSON while inspecting VM storage") from exc
    devices = payload.get("blockdevices")
    if not isinstance(devices, list):
        raise RuntimeError("lsblk did not return a block-device list")
    return [device for device in devices if isinstance(device, dict)]


def _device_path(device: dict[str, Any]) -> str:
    value = device.get("path") or device.get("name")
    if not isinstance(value, str) or not value.startswith("/dev/"):
        raise RuntimeError("lsblk returned an unsafe block-device path")
    return value


def _walk_block_devices(devices: list[dict[str, Any]]):
    """Yield lsblk records recursively, including LVM mapper children."""

    for device in devices:
        yield device
        children = device.get("children") or []
        if isinstance(children, list):
            yield from _walk_block_devices(
                [child for child in children if isinstance(child, dict)]
            )


def _find_device_by_path(device_path: str) -> dict[str, Any] | None:
    """Return one lsblk record for an exact safe device path."""

    matches = []
    for device in _walk_block_devices(_lsblk()):
        value = device.get("path") or device.get("name")
        if value == device_path:
            matches.append(device)
    # lsblk repeats a shared mapper under each of its backing devices.
    if matches and any(device != matches[0] for device in matches[1:]):
        raise RuntimeError(f"lsblk returned conflicting device paths: {device_path}")
    return matches[0] if matches else None


def _has_mountpoint(device: dict[str, Any]) -> bool:
    value = device.get("mountpoints")
    if isinstance(value, list):
        return any(isinstance(item, str) and item for item in value)
    return isinstance(value, str) and bool(value)


def _find_declared_disk(serial: str, expected_size: str) -> dict[str, Any]:
    # Both generations may coexist, but never for the same logical disk.
    serials = storage_disk_serials(serial[3:]) if serial.startswith(("bw-", "it-")) else (serial,)
    matches = [
        device
        for device in _lsblk()
        if device.get("type") == "disk" and device.get("serial") in serials
    ]
    if not matches:
        raise _MissingDeclaredDisk(
            f"Expected exactly one VM data disk with serial {serial!r}; found 0"
        )
    if len(matches) != 1:
        raise RuntimeError(
            f"Expected exactly one VM data disk with serial {serial!r}; found {len(matches)}"
        )
    disk = matches[0]
    try:
        actual_bytes = int(disk.get("size"))
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"VM data disk {serial!r} did not report a valid size") from exc
    expected_bytes = storage_size_kib(expected_size) * 1024
    if actual_bytes < expected_bytes:
        raise RuntimeError(
            f"VM data disk {serial!r} is smaller than declared: "
            f"{actual_bytes} bytes < {expected_bytes} bytes"
        )
    return disk


def _wait_for_declared_disk(
    serial: str,
    expected_size: str,
    *,
    attempts: int = 10,
) -> dict[str, Any]:
    """Wait briefly for a newly hot-added managed disk to reach the guest."""

    last_error: RuntimeError | None = None
    for attempt in range(attempts):
        try:
            return _find_declared_disk(serial, expected_size)
        except _MissingDeclaredDisk as exc:
            last_error = exc
        if attempt + 1 < attempts:
            _run_capture("udevadm settle", check=False)
            time.sleep(1)
    assert last_error is not None
    raise last_error


def _wipefs_signatures(device_path: str) -> list[str]:
    result = _run_capture(
        f"wipefs --no-act --noheadings --output TYPE {shlex.quote(device_path)}",
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip() or "wipefs failed"
        raise RuntimeError(f"Could not inspect {device_path} for signatures: {detail}")
    return [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]


def _partition_for_mount(
    disk: dict[str, Any],
    serial: str,
    expected_size: str,
) -> str:
    disk_path = _device_path(disk)
    children = disk.get("children") or []
    if not isinstance(children, list):
        raise RuntimeError(f"VM data disk {serial!r} returned invalid partition data")

    if not children:
        if any((disk.get("fstype"), disk.get("pttype"), _has_mountpoint(disk))):
            raise RuntimeError(
                f"Refusing to partition nonblank VM data disk {serial!r} ({disk_path})"
            )
        signatures = _wipefs_signatures(disk_path)
        if signatures:
            raise RuntimeError(
                f"Refusing to partition VM data disk {serial!r}; signatures found: "
                + ", ".join(signatures)
            )
        _run_capture(
            f"parted --script {shlex.quote(disk_path)} "
            "mklabel gpt mkpart primary 1MiB 100%"
        )
        _run_capture("udevadm settle")
        disk = _find_declared_disk(serial, expected_size)
        children = disk.get("children") or []

    if disk.get("pttype") != "gpt" or len(children) != 1:
        raise RuntimeError(
            f"VM data disk {serial!r} must contain exactly one GPT partition"
        )
    partition = children[0]
    if not isinstance(partition, dict) or partition.get("type") != "part":
        raise RuntimeError(f"VM data disk {serial!r} has an unexpected child device")
    return _device_path(partition)


def _filesystem_uuid(device_path: str) -> str:
    result = _run_capture(
        f"blkid --match-tag UUID --output value {shlex.quote(device_path)}",
        check=False,
    )
    uuid = (result.stdout or "").strip()
    if result.returncode != 0 or not re.fullmatch(r"[A-Fa-f0-9-]{8,64}", uuid):
        raise RuntimeError(f"Could not read a filesystem UUID from {device_path}")
    return uuid


def _ensure_filesystem(device_path: str, filesystem: str) -> str:
    partition = _find_device_by_path(device_path)
    if partition is None:
        raise RuntimeError(f"Block device disappeared before formatting: {device_path}")

    current_filesystem = partition.get("fstype")
    if current_filesystem:
        if current_filesystem != filesystem:
            raise RuntimeError(
                f"{device_path} contains {current_filesystem}, expected {filesystem}; "
                "refusing to reformat"
            )
        return _filesystem_uuid(device_path)

    signatures = _wipefs_signatures(device_path)
    if signatures:
        raise RuntimeError(
            f"Refusing to format {device_path}; signatures found: "
            + ", ".join(signatures)
        )
    if _has_mountpoint(partition):
        raise RuntimeError(f"Refusing to format mounted partition {device_path}")

    if filesystem == "ext4":
        _run_capture(f"mkfs.ext4 -F -m 0 {shlex.quote(device_path)}")
    elif filesystem == "xfs":
        _run_capture(f"mkfs.xfs -f {shlex.quote(device_path)}")
    else:
        raise RuntimeError(f"Unsupported VM data filesystem: {filesystem}")
    _run_capture("udevadm settle")
    return _filesystem_uuid(device_path)


def _lvm_volume_group(data_name: str) -> str:
    """Return a collision-free LVM VG name for a validated disk name."""

    return f"basaltwater_{data_name.replace('-', '_')}"


def _assert_blank_lvm_disk(disk: dict[str, Any], serial: str) -> str:
    """Return a whole-disk path only when it is safe for first-time pvcreate."""

    disk_path = _device_path(disk)
    if any(
        (
            disk.get("children"),
            disk.get("fstype"),
            disk.get("pttype"),
            _has_mountpoint(disk),
        )
    ):
        raise RuntimeError(
            f"Refusing nonblank VM cache disk {serial!r} ({disk_path})"
        )
    signatures = _wipefs_signatures(disk_path)
    if signatures:
        raise RuntimeError(
            f"Refusing VM cache disk {serial!r}; signatures found: "
            + ", ".join(signatures)
        )
    return disk_path


def _verify_lvm_physical_volume(device_path: str, volume_group: str) -> None:
    result = _run_capture(
        "pvs --noheadings --readonly --options vg_name -- "
        f"{shlex.quote(device_path)}",
        check=False,
    )
    observed = (result.stdout or "").strip()
    if result.returncode != 0 or observed != volume_group:
        raise RuntimeError(
            f"LVM cache volume group {volume_group} does not own {device_path}"
        )


def _prepare_lvm_cache(
    cache: VMStorageCache,
    *,
    data_disk: dict[str, Any],
    cache_disk: dict[str, Any],
    prior: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create or verify one whole-disk LVM cache mapping."""

    volume_group = _lvm_volume_group(cache.data_name)
    legacy_group = f"it_{cache.data_name.replace('-', '_')}"
    if prior is not None:
        if prior.get("cache_name") != cache.cache_name:
            raise RuntimeError("Recorded VM cache device selection changed")
        if prior.get("volume_group") not in {volume_group, legacy_group}:
            raise RuntimeError("Invalid recorded VM cache volume group")
        volume_group = prior["volume_group"]
    elif data_disk.get("fstype") == "LVM2_member":
        observed = _run_capture(
            "pvs --noheadings --readonly --options vg_name -- "
            f"{shlex.quote(_device_path(data_disk))}", check=False,
        )
        if observed.returncode != 0 or (observed.stdout or "").strip() not in {volume_group, legacy_group}:
            raise RuntimeError("Could not identify the existing VM cache volume group")
        volume_group = observed.stdout.strip()
    logical_volume = f"{volume_group}/data"
    mapped_path = f"/dev/mapper/{volume_group}-data"
    data_path = _device_path(data_disk)
    cache_path = _device_path(cache_disk)

    existing = _run_capture(
        "lvs --noheadings --readonly --options cache_mode -- "
        f"{shlex.quote(logical_volume)}",
        check=False,
    )
    if existing.returncode == 0:
        observed_mode = (existing.stdout or "").strip()
        if observed_mode != cache.mode:
            raise RuntimeError(
                f"LVM cache {logical_volume} uses {observed_mode or 'no cache mode'}, "
                f"expected {cache.mode}"
            )
        _verify_lvm_physical_volume(data_path, volume_group)
        _verify_lvm_physical_volume(cache_path, volume_group)
    else:
        if prior is not None:
            raise RuntimeError(f"Recorded LVM cache is missing: {logical_volume}")
        data_path = _assert_blank_lvm_disk(data_disk, str(data_disk.get("serial")))
        cache_path = _assert_blank_lvm_disk(cache_disk, str(cache_disk.get("serial")))
        created_vg = False
        created_pvs = False
        try:
            _run_capture(
                "pvcreate --yes --zero y -- "
                f"{shlex.quote(data_path)} {shlex.quote(cache_path)}"
            )
            created_pvs = True
            _run_capture(
                f"vgcreate {shlex.quote(volume_group)} -- "
                f"{shlex.quote(data_path)} {shlex.quote(cache_path)}"
            )
            created_vg = True
            _run_capture(
                "lvcreate --yes --name data --extents 100%PVS "
                f"{shlex.quote(volume_group)} -- {shlex.quote(data_path)}"
            )
            _run_capture(
                "lvcreate --yes --name cache --extents 100%PVS "
                f"{shlex.quote(volume_group)} -- {shlex.quote(cache_path)}"
            )
            _run_capture(
                "lvconvert --yes --type cache "
                f"--cachevol {shlex.quote(volume_group + '/cache')} "
                f"--cachemode {shlex.quote(cache.mode)} "
                f"{shlex.quote(logical_volume)}"
            )
            _run_capture("udevadm settle")
        except Exception:
            if created_vg:
                removed = _run_capture(
                    f"vgremove --yes --force {shlex.quote(volume_group)}",
                    check=False,
                )
                if removed.returncode != 0:
                    # Preserve PV labels if their VG could not be removed.
                    raise
            if created_pvs:
                _run_capture(
                    "pvremove --yes -- "
                    f"{shlex.quote(data_path)} {shlex.quote(cache_path)}",
                    check=False,
                )
            raise

    if _find_device_by_path(mapped_path) is None:
        raise RuntimeError(f"LVM cache mapping is not visible: {mapped_path}")
    return {
        "data_name": cache.data_name,
        "cache_name": cache.cache_name,
        "mode": cache.mode,
        "volume_group": volume_group,
        "logical_volume": logical_volume,
        "device": mapped_path,
        "data_device": data_path,
        "cache_device": cache_path,
        "data_serial": data_disk.get("serial"),
        "cache_serial": cache_disk.get("serial"),
    }


def _reject_symlinked_mount_path(path: str) -> None:
    current = os.path.sep
    for component in os.path.abspath(path).split(os.path.sep):
        if not component:
            continue
        current = os.path.join(current, component)
        if os.path.lexists(current) and os.path.islink(current):
            raise RuntimeError(f"Refusing symlinked VM storage mount path: {current}")


def _mounted_info(path: str) -> tuple[str, str] | None:
    result = _run_capture(
        f"findmnt --json --target {shlex.quote(path)} --output SOURCE,FSTYPE,TARGET",
        check=False,
    )
    if result.returncode != 0:
        return None
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError("findmnt returned invalid JSON") from exc
    filesystems = payload.get("filesystems")
    if not isinstance(filesystems, list) or not filesystems:
        return None
    record = filesystems[0]
    if not isinstance(record, dict) or record.get("target") != path:
        return None
    source = record.get("source")
    filesystem = record.get("fstype")
    if not isinstance(source, str) or not isinstance(filesystem, str):
        raise RuntimeError(f"findmnt returned incomplete mount data for {path}")
    return source, filesystem


def _systemd_mount_unit(path: str) -> str:
    result = _run_capture(
        f"systemd-escape --path --suffix=mount {shlex.quote(path)}"
    )
    unit = (result.stdout or "").strip()
    if not unit.endswith(".mount") or "/" in unit or any(
        ord(character) < 32 for character in unit
    ):
        raise RuntimeError(f"systemd returned an invalid mount unit for {path}")
    return unit


def _verify_active_mount(
    mount: VMStorageMount,
    expected_uuid: str,
) -> tuple[str, str]:
    mounted = _mounted_info(mount.path)
    if mounted is None:
        raise RuntimeError(f"Required VM data mount is not active: {mount.path}")
    source, filesystem = mounted
    if filesystem != mount.filesystem:
        raise RuntimeError(
            f"{mount.path} is mounted as {filesystem}, expected {mount.filesystem}"
        )
    actual_uuid = _filesystem_uuid(source)
    if actual_uuid != expected_uuid:
        raise RuntimeError(
            f"{mount.path} is backed by UUID {actual_uuid}, expected {expected_uuid}"
        )
    return source, filesystem


def _prepare_mount(
    mount: VMStorageMount,
    disk_size: str,
    serial: str,
    bus_slot: str,
    prepared_device: str | None = None,
    cache_record: dict[str, Any] | None = None,
    prior: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _reject_symlinked_mount_path(mount.path)
    mounted = _mounted_info(mount.path)
    if mounted is None and os.path.lexists(mount.path):
        if not os.path.isdir(mount.path):
            raise RuntimeError(f"VM storage mount path is not a directory: {mount.path}")
        if os.listdir(mount.path):
            raise RuntimeError(
                f"VM storage mount path must be empty before first use: {mount.path}"
            )

    disk = _wait_for_declared_disk(serial, disk_size)
    serial = str(disk.get("serial") or serial)
    if prior is not None:
        if prior.get("serial") != serial:
            raise RuntimeError(f"Recorded VM disk identity changed for {mount.name}")
        # A missing filesystem on a previously managed disk is not a blank-disk
        # initialization request. Check before any partitioning or formatting.
        if not disk.get("children"):
            raise RuntimeError(f"Recorded VM disk is now blank: {mount.name}")
        existing_path = prepared_device or _partition_for_mount(disk, serial, disk_size)
        if _filesystem_uuid(existing_path) != prior.get("uuid"):
            raise RuntimeError(f"Recorded VM filesystem changed for {mount.name}")
    if mounted is not None and not (disk.get("children") or []):
        raise RuntimeError(
            f"Refusing to prepare blank VM data disk {serial!r} while "
            f"{mount.path} is already mounted"
        )
    partition_path = prepared_device or _partition_for_mount(
        disk,
        serial,
        disk_size,
    )
    filesystem_uuid = _ensure_filesystem(partition_path, mount.filesystem)

    if mounted is None:
        if not os.path.lexists(mount.path):
            os.makedirs(mount.path, mode=0o755)
    else:
        _verify_active_mount(mount, filesystem_uuid)

    unit = _systemd_mount_unit(mount.path)
    unit_path = os.path.join("/etc/systemd/system", unit)
    unit_text = (
        "[Unit]\n"
        f"Description=basaltwater VM data mount {mount.name}\n"
        "Before=local-fs.target\n\n"
        "[Mount]\n"
        f"What=/dev/disk/by-uuid/{filesystem_uuid}\n"
        f"Where={mount.path}\n"
        f"Type={mount.filesystem}\n"
        "Options=defaults\n\n"
        "[Install]\n"
        "WantedBy=local-fs.target\n"
    )
    write_text_atomic(unit_path, unit_text, mode=0o644)
    _run_capture("systemctl daemon-reload")
    _run_capture(f"systemctl enable --now {shlex.quote(unit)}")
    source, _filesystem = _verify_active_mount(mount, filesystem_uuid)

    marker = {
        "schema_version": STORAGE_SCHEMA_VERSION,
        "name": mount.name,
        "serial": serial,
        "bus_slot": bus_slot,
        "uuid": filesystem_uuid,
        "filesystem": mount.filesystem,
        "mount_path": mount.path,
    }
    if cache_record is not None:
        marker["cache"] = cache_record
    write_json_atomic(
        os.path.join(mount.path, STORAGE_MARKER),
        marker,
        mode=0o644,
        sort_keys=True,
    )
    return {
        **marker,
        "partition": partition_path,
        "active_source": source,
        "mount_unit": unit,
        "policy": mount.policy,
    }


def setup_vm_storage(config: SetupConfig) -> None:
    """Prepare every declared blank VM data disk before application setup."""

    mounts = storage_mounts(config)
    if not mounts:
        return
    caches = storage_caches(config)
    if is_dry_run():
        for cache in caches:
            print(
                f"  [DRY-RUN] Would cache VM data disk {cache.data_name} "
                f"with {cache.cache_name} ({cache.mode})"
            )
        for mount in mounts:
            print(
                f"  [DRY-RUN] Would prepare VM data disk {mount.name} at "
                f"{mount.path} ({mount.filesystem})"
            )
        return

    disk_by_name = {disk.name: disk for disk in data_disks(config)}
    previous = _load_storage_state()
    old_mounts = {record["name"]: record for record in previous.get("mounts", [])}
    old_caches = {record["data_name"]: record for record in previous.get("caches", [])}
    for name, record in old_mounts.items():
        if name in disk_by_name:
            disk = disk_by_name[name]
            observed = _find_declared_disk(disk.serial, disk.size)
            if observed.get("serial") != record["serial"]:
                raise RuntimeError(f"Recorded VM disk identity changed for {name}")
    for cache in caches:
        prior = old_caches.get(cache.data_name)
        if prior is None:
            continue
        for name, field in ((cache.data_name, "data_serial"), (cache.cache_name, "cache_serial")):
            if prior.get(field) is not None:
                disk = disk_by_name[name]
                if _find_declared_disk(disk.serial, disk.size).get("serial") != prior[field]:
                    raise RuntimeError(f"Recorded VM cache disk identity changed for {name}")
    disk_slots = {
        disk.name: f"scsi{index}"
        for index, disk in enumerate(data_disks(config), 1)
    }
    packages = "parted e2fsprogs xfsprogs util-linux"
    if caches:
        packages += " lvm2 thin-provisioning-tools"
    dependencies = _run_capture(
        f"apt-get install -y -qq {packages}"
    )
    if dependencies.returncode != 0:
        raise RuntimeError("VM storage setup dependencies could not be installed")

    cache_records: list[dict[str, Any]] = []
    cache_by_data_name: dict[str, dict[str, Any]] = {}
    for cache in caches:
        if cache.mode == "writeback":
            print(
                "  ! LVM writeback caching accepts extra data-loss risk if the "
                "cache device fails; use writethrough unless that risk is intentional"
            )
        data_disk = disk_by_name.get(cache.data_name)
        cache_disk = disk_by_name.get(cache.cache_name)
        if data_disk is None or cache_disk is None:
            raise RuntimeError(
                f"Incomplete VM cache declaration for {cache.data_name}"
            )
        cache_record = _prepare_lvm_cache(
            cache,
            data_disk=_find_declared_disk(data_disk.serial, data_disk.size),
            cache_disk=_find_declared_disk(cache_disk.serial, cache_disk.size),
            prior=old_caches.get(cache.data_name),
        )
        cache_record.update(
            {
                "data_pool": data_disk.pool,
                "cache_pool": cache_disk.pool,
                "data_requested_size": data_disk.size,
                "cache_requested_size": cache_disk.size,
            }
        )
        cache_records.append(cache_record)
        cache_by_data_name[cache.data_name] = cache_record
        print(
            f"  Cached VM data disk {cache.data_name} with "
            f"{cache.cache_name} ({cache.mode})"
        )

    records: list[dict[str, Any]] = []
    for mount in mounts:
        disk = disk_by_name.get(mount.name)
        if disk is None:
            raise RuntimeError(f"No VM data disk declaration exists for {mount.name}")
        cache_record = cache_by_data_name.get(mount.name)
        record = _prepare_mount(
            mount,
            disk.size,
            disk.serial,
            disk_slots[mount.name],
            prepared_device=(
                str(cache_record["device"])
                if cache_record is not None
                else None
            ),
            cache_record=cache_record,
            prior=old_mounts.get(mount.name),
        )
        records.append({**record, "pool": disk.pool, "requested_size": disk.size})
        print(f"  Mounted VM data disk {mount.name} at {mount.path}")

    write_json_atomic(
        STORAGE_STATE_FILE,
        {
            "schema_version": STORAGE_SCHEMA_VERSION,
            "mounts": records,
            "caches": cache_records,
        },
        mode=0o600,
        sort_keys=True,
    )


def assert_declared_storage_mount(config: SetupConfig, path: str) -> None:
    """Verify the declared mount containing ``path`` before an application write."""

    matching = [
        mount
        for mount in storage_mounts(config)
        if path == mount.path or path.startswith(mount.path + os.sep)
    ]
    if not matching:
        return
    mount = max(matching, key=lambda item: len(item.path))
    marker_path = os.path.join(mount.path, STORAGE_MARKER)
    try:
        with open(marker_path, encoding="utf-8") as file_obj:
            marker = json.load(file_obj)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"Required VM storage marker is missing or invalid: {marker_path}"
        ) from exc
    if not isinstance(marker, dict) or marker.get("name") != mount.name:
        raise RuntimeError(f"VM storage marker does not match {mount.name}: {marker_path}")
    expected_uuid = marker.get("uuid")
    if not isinstance(expected_uuid, str) or not expected_uuid:
        raise RuntimeError(f"VM storage marker has no UUID: {marker_path}")
    _verify_active_mount(mount, expected_uuid)
