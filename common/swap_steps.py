"""Rerun-safe, ownership-aware Linux swap configuration."""

from __future__ import annotations

import json
import os
import re
import shlex
import stat
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from common.storage_steps import (
    _device_path,
    _find_declared_disk,
    _find_device_by_path,
    _has_mountpoint,
    _wipefs_signatures,
)
from lib.atomic_io import (
    _fsync_directory,
    remove_file_durable,
    write_json_atomic,
    write_text_atomic,
)
from lib.config import SetupConfig
from lib.machine_state import can_manage_swap
from lib.remote_utils import is_dry_run, run
from lib.swap_config import (
    SwapDevice,
    SwapFile,
    SwapZram,
    has_explicit_swap_areas,
    swap_devices,
    swap_files,
    swap_zram,
)
from lib.vm_storage import data_disks


SWAP_STATE_FILE = "/opt/basaltwater/state/swap.json"
FSTAB_PATH = "/etc/fstab"
SYSCTL_PATH = "/etc/sysctl.d/90-basaltwater-swap.conf"
ZRAM_PATH = "/etc/systemd/zram-generator.conf.d/90-basaltwater.conf"
ZSWAP_SERVICE_PATH = "/etc/systemd/system/basaltwater-zswap.service"
RESUME_PATH = "/etc/initramfs-tools/conf.d/99-basaltwater-resume"
SWAP_SCHEMA_VERSION = 1
FSTAB_BEGIN = "# BEGIN basaltwater managed swap"
FSTAB_END = "# END basaltwater managed swap"
_STATE_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
_STATE_SIZE_PATTERN = re.compile(r"^[1-9][0-9]*[KMGT]$", re.IGNORECASE)
_STATE_UUID_PATTERN = re.compile(r"^[A-Za-z0-9-]{8,64}$")


def _capture(command: str, *, check: bool = True):
    return run(command, check=check, capture_output=True)


def get_total_ram_mb() -> int:
    result = _capture("free --mebi --output=total | tail -n 1", check=False)
    try:
        return int(result.stdout.strip())
    except (ValueError, AttributeError):
        return 0


def get_free_disk_mb() -> int:
    result = _capture("df --output=avail -BM / | tail -n 1", check=False)
    match = re.search(r"([0-9]+)", result.stdout or "")
    return int(match.group(1)) if match else 0


def _load_state() -> dict[str, Any]:
    try:
        with open(SWAP_STATE_FILE, encoding="utf-8") as state_file:
            state = json.load(state_file)
    except FileNotFoundError:
        return {"schema": SWAP_SCHEMA_VERSION, "areas": []}
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("Could not read the managed swap state") from exc
    if not isinstance(state, dict) or state.get("schema") != SWAP_SCHEMA_VERSION:
        raise RuntimeError("Managed swap state has an unsupported schema")
    state["areas"] = _validate_state_areas(state.get("areas"))
    return state


def _validate_state_areas(raw_areas: object) -> list[dict[str, Any]]:
    if not isinstance(raw_areas, list):
        raise RuntimeError("Managed swap state has an invalid area list")
    areas: list[dict[str, Any]] = []
    resources: set[tuple[str, str]] = set()
    for raw_area in raw_areas:
        area = _validate_state_area(raw_area)
        area_resources = _state_resources(area)
        if resources & area_resources:
            raise RuntimeError("Managed swap state contains a duplicate resource")
        resources.update(area_resources)
        areas.append(area)
    return areas


def _validate_state_area(raw_area: object) -> dict[str, Any]:
    if not isinstance(raw_area, dict):
        raise RuntimeError("Managed swap state contains a non-object area")
    area = dict(raw_area)
    name = area.get("name")
    area_type = area.get("type")
    source = area.get("source")
    priority = area.get("priority")
    if not isinstance(name, str) or not _STATE_NAME_PATTERN.fullmatch(name):
        raise RuntimeError("Managed swap state contains an invalid area name")
    if area_type not in {"file", "device", "zram"}:
        raise RuntimeError(f"Managed swap state area '{name}' has an invalid type")
    if not isinstance(source, str) or not source:
        raise RuntimeError(f"Managed swap state area '{name}' has no source")
    if (
        isinstance(priority, bool)
        or not isinstance(priority, int)
        or not 0 <= priority <= 32767
    ):
        raise RuntimeError(f"Managed swap state area '{name}' has an invalid priority")
    if area_type in {"file", "zram"}:
        size = area.get("size")
        if not isinstance(size, str) or not _STATE_SIZE_PATTERN.fullmatch(size):
            raise RuntimeError(f"Managed swap state area '{name}' has an invalid size")
        if size[-1].upper() == "K" and int(size[:-1]) % 1024:
            raise RuntimeError(
                f"Managed swap state area '{name}' size is not a whole MiB"
            )
    if area_type == "file":
        if (
            not os.path.isabs(source)
            or os.path.normpath(source) != source
            or source == "/"
        ):
            raise RuntimeError(f"Managed swap state area '{name}' has an invalid path")
        if area.get("pending") not in {None, True, False}:
            raise RuntimeError(
                f"Managed swap state area '{name}' has an invalid pending marker"
            )
    elif area_type == "device":
        path = area.get("path")
        uuid = area.get("uuid")
        discard = area.get("discard", "off")
        if (
            not isinstance(path, str)
            or not path.startswith("/dev/")
            or os.path.normpath(path) != path
        ):
            raise RuntimeError(
                f"Managed swap state area '{name}' has an invalid device path"
            )
        if not isinstance(uuid, str) or not _STATE_UUID_PATTERN.fullmatch(uuid):
            raise RuntimeError(f"Managed swap state area '{name}' has an invalid UUID")
        if discard not in {"off", "once", "pages", "both"}:
            raise RuntimeError(
                f"Managed swap state area '{name}' has an invalid discard policy"
            )
        if not isinstance(area.get("provider_owned", False), bool):
            raise RuntimeError(
                f"Managed swap state area '{name}' has invalid ownership metadata"
            )
        if area.get("provider_owned") is True:
            serial = area.get("serial")
            size = area.get("size")
            # States written before provider identity metadata was introduced
            # have neither field. Accept that pair so they can be upgraded on
            # the next successful reconciliation, but never accept half of it.
            legacy_metadata = serial is None and size is None
            valid_metadata = (
                isinstance(serial, str)
                and re.fullmatch(r"it-[a-z][a-z0-9-]{0,16}", serial) is not None
                and isinstance(size, str)
                and _STATE_SIZE_PATTERN.fullmatch(size) is not None
            )
            if not legacy_metadata and not valid_metadata:
                raise RuntimeError(
                    f"Managed swap state area '{name}' has invalid provider metadata"
                )
    else:
        if not re.fullmatch(r"/dev/zram(?:0|[1-9][0-9]*)", source):
            raise RuntimeError(
                f"Managed swap state area '{name}' has an invalid zram source"
            )
        algorithm = area.get("algorithm", "auto")
        if not isinstance(algorithm, str) or not re.fullmatch(
            r"(?:auto|[a-z0-9_-]{1,32})", algorithm
        ):
            raise RuntimeError(
                f"Managed swap state area '{name}' has an invalid algorithm"
            )
    return area


def _state_resources(area: dict[str, Any]) -> set[tuple[str, str]]:
    area_type = str(area["type"])
    if area_type == "device":
        return {
            (area_type, str(area[key]))
            for key in ("source", "path", "uuid")
            if area.get(key)
        }
    return {(area_type, str(area["source"]))}


def _same_resource(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if left.get("type") != right.get("type"):
        return False
    if left.get("type") != "device":
        return left.get("source") == right.get("source")
    return any(
        bool(left.get(key)) and left.get(key) == right.get(key)
        for key in ("source", "path", "uuid")
    )


def _write_state(areas: list[dict[str, Any]]) -> None:
    validated_areas = _validate_state_areas(areas)
    write_json_atomic(
        SWAP_STATE_FILE,
        {"schema": SWAP_SCHEMA_VERSION, "areas": validated_areas},
        mode=0o600,
        sort_keys=True,
    )


def _ownership_union(
    current: list[dict[str, Any]], previous: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Retain old ownership until its resource is replaced or removed."""

    return [
        *current,
        *[
            area
            for area in previous
            if not any(_same_resource(area, item) for item in current)
        ],
    ]


def _prior_resource(
    areas: list[dict[str, Any]], area_type: str, source: str
) -> dict[str, Any] | None:
    return next(
        (
            area
            for area in areas
            if area.get("type") == area_type and area.get("source") == source
        ),
        None,
    )


def _active_swap() -> set[str]:
    return set(_active_swap_inventory())


def _active_swap_inventory() -> dict[str, int | None]:
    result = _capture(
        "swapon --show=NAME,PRIO --noheadings --raw",
        check=False,
    )
    if result.returncode not in {0, 1}:
        raise RuntimeError("Could not inspect active swap areas")
    inventory: dict[str, int | None] = {}
    for line in (result.stdout or "").splitlines():
        fields = line.rsplit(maxsplit=1)
        if len(fields) != 2:
            raise RuntimeError("swapon returned an invalid swap priority")
        try:
            priority = int(fields[1])
        except ValueError as exc:
            raise RuntimeError("swapon returned an invalid swap priority") from exc
        inventory[fields[0]] = priority
    return inventory


def _replace_fstab(entries: list[str]) -> None:
    try:
        current = Path(FSTAB_PATH).read_text(encoding="utf-8")
    except FileNotFoundError:
        current = ""
    start = current.find(FSTAB_BEGIN)
    end = current.find(FSTAB_END)
    if (start == -1) != (end == -1) or (start != -1 and end < start):
        raise RuntimeError("/etc/fstab contains an incomplete basaltwater swap block")
    if start != -1:
        end += len(FSTAB_END)
        while end < len(current) and current[end] == "\n":
            end += 1
        current = current[:start].rstrip("\n") + current[end:]
    block = ""
    if entries:
        block = "\n".join((FSTAB_BEGIN, *entries, FSTAB_END)) + "\n"
    content = current.rstrip("\n")
    if content and block:
        content += "\n\n"
    elif content:
        content += "\n"
    content += block
    write_text_atomic(FSTAB_PATH, content, mode=0o644)


def _swap_uuid(source: str) -> str:
    result = _capture(
        f"blkid --match-tag UUID --output value {shlex.quote(source)}",
        check=False,
    )
    value = (result.stdout or "").strip()
    if result.returncode != 0 or not re.fullmatch(r"[A-Za-z0-9-]{8,64}", value):
        raise RuntimeError(f"Could not read the swap UUID from {source}")
    return value


def _swap_type(source: str) -> str | None:
    result = _capture(
        f"blkid --probe --match-tag TYPE --output value {shlex.quote(source)}",
        check=False,
    )
    value = (result.stdout or "").strip()
    return value if result.returncode == 0 and value else None


def _swapon(source: str, priority: int, discard: str = "off") -> None:
    options = ["swapon", "--priority", str(priority)]
    if discard != "off":
        options.append("--discard" if discard == "both" else f"--discard={discard}")
    options.append(source)
    run(shlex.join(options))


def _warn_swap_file_filesystem(path: str) -> None:
    target = path if os.path.exists(path) else os.path.dirname(path)
    result = _capture(
        f"findmnt --noheadings --output FSTYPE --target {shlex.quote(target)}",
        check=False,
    )
    filesystem = (result.stdout or "").strip().lower()
    if filesystem == "zfs":
        print(
            f"  ⚠ Swap file {path} is on ZFS; basaltwater has not qualified "
            "ZFS swap-file behavior. Prefer a dedicated block device."
        )
    elif filesystem == "btrfs":
        raise RuntimeError(
            f"Swap file {path} is on Btrfs, which requires filesystem-specific "
            "creation rules not managed by basaltwater"
        )


def _size_mib(value: str) -> int:
    units_kib = {"K": 1, "M": 1024, "G": 1024 * 1024, "T": 1024 * 1024 * 1024}
    return int(value[:-1]) * units_kib[value[-1].upper()] // 1024


def _swap_label(name: str) -> str:
    return f"it-{name}"[:16]


def _assert_safe_swap_parent(path: str) -> None:
    """Reject swap files below directories writable by non-root users."""

    candidate = Path(path)
    while not candidate.exists():
        candidate = candidate.parent
    info = candidate.stat()
    if info.st_uid != 0 or info.st_mode & 0o022:
        raise RuntimeError(
            f"Swap-file parent {candidate} must be root-owned and not writable "
            "by group or other users"
        )


def _stage_swap_file(area: SwapFile, parent: str, size_mib: int) -> str:
    """Create and validate a complete same-filesystem replacement file."""

    descriptor, staged_path = tempfile.mkstemp(
        dir=parent,
        prefix=f".{os.path.basename(area.path)}.basaltwater-",
    )
    os.close(descriptor)
    try:
        run(f"fallocate --length {size_mib}M {shlex.quote(staged_path)}")
        run(f"chmod 600 {shlex.quote(staged_path)}")
        run(
            f"mkswap --label {shlex.quote(_swap_label(area.name))} "
            f"{shlex.quote(staged_path)}"
        )
        if _swap_type(staged_path) != "swap":
            raise RuntimeError(
                f"Staged swap file did not acquire a swap signature: {area.path}"
            )
        staged_descriptor = os.open(staged_path, os.O_RDONLY)
        try:
            os.fsync(staged_descriptor)
        finally:
            os.close(staged_descriptor)
        return staged_path
    except Exception:
        remove_file_durable(staged_path)
        raise


def _ensure_swap_file(area: SwapFile, prior: dict[str, Any] | None) -> dict[str, Any]:
    path = area.path
    parent = os.path.dirname(path)
    if os.path.lexists(path) and os.path.islink(path):
        raise RuntimeError(f"Refusing to manage symlink swap file: {path}")
    probe = Path(parent)
    while probe != probe.parent:
        if probe.is_symlink():
            raise RuntimeError(f"Refusing swap path below symlink: {probe}")
        probe = probe.parent
    _assert_safe_swap_parent(parent)
    os.makedirs(parent, mode=0o755, exist_ok=True)
    _warn_swap_file_filesystem(path)
    size_mib = _size_mib(area.size)
    existed = os.path.exists(path)
    was_active = False
    replace = not existed
    if existed:
        file_info = os.stat(path, follow_symlinks=False)
        if (
            not stat.S_ISREG(file_info.st_mode)
            or file_info.st_nlink != 1
            or file_info.st_uid != 0
        ):
            raise RuntimeError(f"Managed swap path is not a single regular file: {path}")
        if not prior or prior.get("type") != "file" or prior.get("source") != path:
            raise RuntimeError(
                f"Refusing to adopt existing unmanaged swap file {path}; choose "
                "another path or remove it explicitly"
            )
        if _swap_type(path) != "swap":
            raise RuntimeError(
                f"Managed swap file {path} no longer has a swap signature; "
                "refusing to overwrite or remove it"
            )
        replace = os.path.getsize(path) != size_mib * 1024 * 1024

    inventory = _active_swap_inventory()
    active = set(inventory)
    was_active = path in active
    priority_changed = bool(
        (prior and prior.get("priority") != area.priority)
        or (was_active and inventory.get(path) != area.priority)
    )
    previous_priority = inventory.get(path)
    if previous_priority is None:
        previous_priority = int(prior.get("priority", 100)) if prior else 100
    staged_path: str | None = None
    if replace:
        staged_path = _stage_swap_file(area, parent, size_mib)
    try:
        if was_active and (replace or priority_changed):
            run(f"swapoff {shlex.quote(path)}")
            active.remove(path)
        if staged_path is not None:
            os.replace(staged_path, path)
            _fsync_directory(parent)
        if path not in active:
            _swapon(path, area.priority)
    except Exception:
        if existed and was_active and (staged_path is None or os.path.exists(path)):
            try:
                # A resized replacement is still a complete, validated swap
                # file. Restoring the old live priority keeps memory headroom
                # until the next rerun can apply the desired policy.
                _swapon(path, previous_priority)
            except Exception:
                pass
        raise
    finally:
        if staged_path is not None:
            try:
                remove_file_durable(staged_path)
            except FileNotFoundError:
                pass
    return {
        "name": area.name,
        "type": "file",
        "source": path,
        "priority": area.priority,
        "size": area.size,
    }


def _resolve_direct_device(source: str) -> str:
    if source.startswith("UUID="):
        result = _capture(f"findfs {shlex.quote(source)}", check=False)
        path = (result.stdout or "").strip()
        if result.returncode != 0 or not path.startswith("/dev/"):
            raise RuntimeError(f"Could not resolve swap device {source}")
        return path
    resolved = os.path.realpath(source)
    if not resolved.startswith("/dev/") or resolved == source:
        raise RuntimeError(f"Could not resolve stable swap device path {source}")
    return resolved


def _ensure_swap_device(
    config: SetupConfig,
    area: SwapDevice,
    prior: dict[str, Any] | None,
    known_areas: list[dict[str, Any]] | None = None,
    claimed_areas: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    disks = {disk.name: disk for disk in data_disks(config)}
    provider_owned = area.source in disks
    provider_serial: str | None = None
    provider_size: str | None = None
    if provider_owned:
        declared = disks[area.source]
        record = _find_declared_disk(declared.serial, declared.size)
        path = _device_path(record)
        provider_serial = declared.serial
        provider_size = declared.size
    elif (
        prior
        and prior.get("provider_owned") is True
        and prior.get("source") == area.source
        and isinstance(prior.get("serial"), str)
        and isinstance(prior.get("size"), str)
    ):
        provider_owned = True
        provider_serial = str(prior["serial"])
        provider_size = str(prior["size"])
        record = _find_declared_disk(provider_serial, provider_size)
        path = _device_path(record)
    else:
        path = _resolve_direct_device(area.source)
        record = _find_device_by_path(path)
        if record is None:
            raise RuntimeError(f"Swap device is not visible to lsblk: {path}")

    if path.startswith(("/dev/zvol/", "/dev/zd")):
        print(
            f"  ⚠ Swap device {path} appears to be ZFS-backed. Direct ZFS swap "
            "has not been qualified by basaltwater; monitor pool memory pressure."
        )
    if record.get("children") or _has_mountpoint(record):
        raise RuntimeError(f"Refusing to use partitioned or mounted swap device {path}")
    signatures = _wipefs_signatures(path)
    current_type = record.get("fstype") or _swap_type(path)
    if current_type and current_type != "swap":
        raise RuntimeError(f"Refusing to overwrite {current_type} on swap device {path}")
    non_swap_signatures = [signature for signature in signatures if signature != "swap"]
    if non_swap_signatures:
        raise RuntimeError(
            f"Refusing to overwrite signatures on swap device {path}: "
            + ", ".join(non_swap_signatures)
        )
    if current_type != "swap":
        if not provider_owned and area.name not in (config.swap_initialize or []):
            raise RuntimeError(
                f"Blank direct device {area.source} requires "
                f"--swap-initialize {area.name}"
            )
        run(
            f"mkswap --label {shlex.quote(_swap_label(area.name))} "
            f"{shlex.quote(path)}"
        )
        run("udevadm settle")
    uuid = _swap_uuid(path)
    identity = {
        "name": area.name,
        "type": "device",
        "source": area.source,
        "path": path,
        "uuid": uuid,
    }
    duplicate = next(
        (
            item
            for item in claimed_areas or []
            if _same_resource(item, identity)
        ),
        None,
    )
    if duplicate is not None:
        raise RuntimeError(
            f"Swap areas '{duplicate['name']}' and '{area.name}' resolve to "
            f"the same block device {path}"
        )
    if prior is None:
        prior = next(
            (
                item
                for item in known_areas or []
                if _same_resource(item, identity)
            ),
            None,
        )
    inventory = _active_swap_inventory()
    active = set(inventory)
    active_name = next(
        (
            candidate
            for candidate in (path, f"/dev/disk/by-uuid/{uuid}")
            if candidate in active
        ),
        None,
    )
    prior_policy_changed = bool(
        prior
        and (
            prior.get("priority") != area.priority
            or prior.get("discard", "off") != area.discard
        )
    )
    priority_drifted = bool(
        active_name is not None and inventory.get(active_name) != area.priority
    )
    was_active = active_name is not None
    previous_priority = inventory.get(active_name or "")
    if previous_priority is None:
        previous_priority = int(prior.get("priority", 100)) if prior else 100
    if (prior_policy_changed or priority_drifted) and was_active:
        run(f"swapoff {shlex.quote(path)}")
        active.discard(path)
        active.discard(f"/dev/disk/by-uuid/{uuid}")
    if path not in active and f"/dev/disk/by-uuid/{uuid}" not in active:
        try:
            _swapon(path, area.priority, area.discard)
        except Exception:
            if (prior_policy_changed or priority_drifted) and was_active:
                try:
                    _swapon(
                        path,
                        previous_priority,
                        str(prior.get("discard", "off") if prior else "off"),
                    )
                except Exception:
                    pass
            raise
    result = {
        "name": area.name,
        "type": "device",
        "source": area.source,
        "path": path,
        "uuid": uuid,
        "priority": area.priority,
        "discard": area.discard,
        "provider_owned": provider_owned,
    }
    if provider_owned:
        result["serial"] = provider_serial
        result["size"] = provider_size
    return result


def _zram_configuration(areas: list[SwapZram]) -> str:
    sections: list[str] = []
    for index, area in enumerate(areas):
        lines = [
            f"[zram{index}]",
            f"zram-size = {_size_mib(area.size)}",
            f"swap-priority = {area.priority}",
        ]
        if area.algorithm != "auto":
            lines.append(f"compression-algorithm = {area.algorithm}")
        sections.append("\n".join(lines))
    return "\n\n".join(sections) + ("\n" if sections else "")


def _zram_device_busy(index: int, active: set[str]) -> bool:
    source = f"/dev/zram{index}"
    if source in active:
        return True
    try:
        size = int(
            Path(f"/sys/block/zram{index}/disksize")
            .read_text(encoding="utf-8")
            .strip()
        )
    except (FileNotFoundError, OSError, ValueError):
        return False
    return size > 0


def _reconcile_zram(
    areas: list[SwapZram],
    old_areas: list[dict[str, Any]],
    *,
    journal_ownership: Callable[[list[dict[str, Any]]], None] | None = None,
) -> list[dict[str, Any]]:
    records = [
        {
            "name": area.name,
            "type": "zram",
            "source": f"/dev/zram{index}",
            "priority": area.priority,
            "size": area.size,
            "algorithm": area.algorithm,
        }
        for index, area in enumerate(areas)
    ]
    desired_content = _zram_configuration(areas)
    try:
        previous_content = Path(ZRAM_PATH).read_text(encoding="utf-8")
        previous_exists = True
    except FileNotFoundError:
        previous_content = ""
        previous_exists = False
    old_sources = {str(area.get("source")) for area in old_areas}
    inventory = _active_swap_inventory()
    active = set(inventory)
    for index in range(len(areas)):
        source = f"/dev/zram{index}"
        if source not in old_sources and _zram_device_busy(index, active):
            raise RuntimeError(
                f"Refusing to adopt unmanaged zram device {source}; disable its "
                "existing configuration before declaring managed zram"
            )
    if journal_ownership is not None:
        journal_ownership(records)
    if records == old_areas and previous_content == desired_content:
        restart = [
            index
            for index, area in enumerate(areas)
            if (
                f"/dev/zram{index}" not in active
                or inventory.get(f"/dev/zram{index}") != area.priority
            )
        ]
        if restart:
            run("systemctl daemon-reload")
            for index in restart:
                if f"/dev/zram{index}" in active:
                    run(
                        f"systemctl stop systemd-zram-setup@zram{index}.service",
                        check=False,
                    )
                run(f"systemctl start systemd-zram-setup@zram{index}.service")
            observed = _active_swap_inventory()
            still_incorrect = [
                f"/dev/zram{index}"
                for index in restart
                if observed.get(f"/dev/zram{index}") != areas[index].priority
            ]
            if still_incorrect:
                raise RuntimeError(
                    "Managed zram did not acquire its configured priority: "
                    + ", ".join(still_incorrect)
                )
        return records
    if not areas and not old_areas:
        return []
    if areas:
        run("apt-get install -y systemd-zram-generator")
        write_text_atomic(ZRAM_PATH, _zram_configuration(areas), mode=0o644)
    else:
        remove_file_durable(ZRAM_PATH)
    try:
        for index in range(max(len(old_areas), len(areas))):
            run(f"systemctl stop systemd-zram-setup@zram{index}.service", check=False)
        run("systemctl daemon-reload")
        for index in range(len(areas)):
            run(f"systemctl start systemd-zram-setup@zram{index}.service")
        observed = _active_swap_inventory()
        incorrect_sources = [
            record["source"]
            for record in records
            if observed.get(record["source"]) != record["priority"]
        ]
        if incorrect_sources:
            raise RuntimeError(
                "Managed zram did not acquire its configured priority: "
                + ", ".join(incorrect_sources)
            )
    except Exception:
        for index in range(max(len(old_areas), len(areas))):
            run(
                f"systemctl stop systemd-zram-setup@zram{index}.service",
                check=False,
            )
        if previous_exists:
            write_text_atomic(ZRAM_PATH, previous_content, mode=0o644)
        else:
            remove_file_durable(ZRAM_PATH)
        run("systemctl daemon-reload", check=False)
        for index in range(len(old_areas)):
            run(
                f"systemctl start systemd-zram-setup@zram{index}.service",
                check=False,
            )
        raise
    return records


def _configure_swappiness(value: int | None, *, remove: bool = False) -> None:
    if remove:
        if remove_file_durable(SYSCTL_PATH):
            run("sysctl --system")
        return
    if value is None:
        return
    content = f"vm.swappiness = {value}\n"
    try:
        current = Path(SYSCTL_PATH).read_text(encoding="utf-8")
    except FileNotFoundError:
        current = ""
    if current != content:
        write_text_atomic(SYSCTL_PATH, content, mode=0o644)
    run(f"sysctl --write vm.swappiness={value}")


def _configure_zswap(enabled: bool | None, pool_percent: int | None) -> None:
    if enabled is None:
        return
    if not os.path.exists("/sys/module/zswap/parameters/enabled"):
        if enabled:
            raise RuntimeError("This kernel does not expose zswap controls")
        if os.path.exists(ZSWAP_SERVICE_PATH):
            run("systemctl disable --now basaltwater-zswap.service", check=False)
            remove_file_durable(ZSWAP_SERVICE_PATH)
            run("systemctl daemon-reload")
        return
    state = "Y" if enabled else "N"
    commands = [
        "[Unit]",
        "Description=Apply basaltwater zswap policy",
        "After=systemd-modules-load.service",
        "",
        "[Service]",
        "Type=oneshot",
        "RemainAfterExit=yes",
        f"ExecStart=/bin/sh -c 'printf {state} > /sys/module/zswap/parameters/enabled'",
    ]
    if pool_percent is not None:
        commands.append(
            "ExecStart=/bin/sh -c 'printf "
            f"{pool_percent} > /sys/module/zswap/parameters/max_pool_percent'"
        )
    commands.extend(("", "[Install]", "WantedBy=multi-user.target", ""))
    content = "\n".join(commands)
    try:
        current = Path(ZSWAP_SERVICE_PATH).read_text(encoding="utf-8")
    except FileNotFoundError:
        current = ""
    changed = current != content
    if changed:
        write_text_atomic(ZSWAP_SERVICE_PATH, content, mode=0o644)
        run("systemctl daemon-reload")
    run("systemctl enable basaltwater-zswap.service")
    live_state = Path("/sys/module/zswap/parameters/enabled").read_text(
        encoding="utf-8"
    ).strip().upper()
    live_pool = (
        Path("/sys/module/zswap/parameters/max_pool_percent")
        .read_text(encoding="utf-8")
        .strip()
        if pool_percent is not None
        else None
    )
    if changed or live_state != state or (
        pool_percent is not None and live_pool != str(pool_percent)
    ):
        run("systemctl restart basaltwater-zswap.service")
    else:
        run("systemctl start basaltwater-zswap.service")


def _remove_managed_zswap() -> None:
    if not os.path.exists(ZSWAP_SERVICE_PATH):
        return
    if os.path.exists("/sys/module/zswap/parameters/enabled"):
        run("sh -c 'printf N > /sys/module/zswap/parameters/enabled'")
    run("systemctl disable --now basaltwater-zswap.service", check=False)
    remove_file_durable(ZSWAP_SERVICE_PATH)
    run("systemctl daemon-reload")


def _remove_area(area: dict[str, Any], active: set[str]) -> None:
    source = str(area.get("path") or area.get("source") or "")
    candidates = {source}
    if area.get("uuid"):
        candidates.add(f"/dev/disk/by-uuid/{area['uuid']}")
    if candidates & active:
        run(f"swapoff {shlex.quote(source)}")
    if area.get("type") == "file" and source:
        if not os.path.exists(source):
            return
        if _swap_type(source) != "swap":
            raise RuntimeError(
                f"Managed swap file {source} no longer has a swap signature; "
                "refusing to remove it"
            )
        remove_file_durable(source)


def _fstab_field(value: str) -> str:
    return value.replace("\\", "\\134").replace(" ", "\\040").replace("\t", "\\011")


def _fstab_entry(area: dict[str, Any]) -> str | None:
    if area["type"] == "file":
        return (
            f"{_fstab_field(str(area['source']))} none swap "
            f"sw,nofail,pri={area['priority']} 0 0"
        )
    if area["type"] == "device":
        options = ["sw", "nofail", f"pri={area['priority']}"]
        discard = area.get("discard", "off")
        if discard != "off":
            options.append(
                "discard" if discard == "both" else f"discard={discard}"
            )
        return f"UUID={area['uuid']} none swap {','.join(options)} 0 0"
    return None


def _configure_resume(name: str | None, areas: list[dict[str, Any]]) -> None:
    previous = os.path.exists(RESUME_PATH)
    if not name:
        if previous:
            remove_file_durable(RESUME_PATH)
            run("update-initramfs -u")
        return
    area = next((item for item in areas if item["name"] == name), None)
    if area is None or area["type"] != "device":
        raise RuntimeError("The configured resume swap device was not reconciled")
    content = f"RESUME=UUID={area['uuid']}\n"
    try:
        current = Path(RESUME_PATH).read_text(encoding="utf-8")
    except FileNotFoundError:
        current = ""
    if current != content:
        write_text_atomic(RESUME_PATH, content, mode=0o644)
        run("update-initramfs -u")


def _automatic_swap_file() -> SwapFile | None:
    ram_mb = get_total_ram_mb()
    if ram_mb == 0:
        print("  ⚠ Could not detect RAM size; leaving swap unchanged")
        return None
    size_mb = ram_mb * 2 if ram_mb < 2048 else ram_mb if ram_mb < 8192 else 4096
    free_mb = get_free_disk_mb()
    if free_mb < size_mb + 1024:
        if free_mb <= 2048:
            print(f"  ⚠ Not enough free root storage for swap ({free_mb} MiB free)")
            return None
        size_mb = 1024
        print("  ⚠ Reducing automatic swap to 1024 MiB due to free space")
    return SwapFile("auto-root", "/swapfile", f"{size_mb}M", 100)


def configure_swap(config: SetupConfig) -> None:
    """Reconcile only swap areas explicitly owned by basaltwater."""

    explicit = has_explicit_swap_areas(config)
    if is_dry_run() or config.dry_run:
        count = len(swap_files(config)) + len(swap_devices(config)) + len(swap_zram(config))
        print(f"  [dry-run] swap mode={config.swap_mode}, declared areas={count}")
        return
    if (
        config.system_type == "server_proxmox"
        and not explicit
        and config.swap_mode in {"auto", "preserve"}
    ):
        _configure_swappiness(config.swappiness)
        _configure_zswap(config.zswap, config.zswap_max_pool_percent)
        if config.swap_resume == "":
            _configure_resume(None, [])
        print("  ✓ Preserving Proxmox host swap layout")
        return
    if not can_manage_swap():
        print("  ✓ Skipping swap configuration (managed by container host)")
        return

    state = _load_state()
    old_areas = [item for item in state["areas"] if isinstance(item, dict)]
    active = _active_swap()

    if config.swap_mode == "preserve":
        _configure_swappiness(config.swappiness)
        _configure_zswap(config.zswap, config.zswap_max_pool_percent)
        if config.swap_resume == "":
            _configure_resume(None, [])
        print("  ✓ Preserving existing swap areas")
        return
    if config.swap_mode == "none":
        for area in old_areas:
            if area.get("type") != "zram":
                _remove_area(area, active)
        _reconcile_zram(
            [], [area for area in old_areas if area.get("type") == "zram"]
        )
        _replace_fstab([])
        _configure_resume(None, [])
        _configure_swappiness(None, remove=True)
        _remove_managed_zswap()
        remove_file_durable(SWAP_STATE_FILE)
        print("  ✓ Removed basaltwater-managed swap areas")
        return

    desired_files = swap_files(config)
    desired_devices = swap_devices(config)
    desired_zram = swap_zram(config)
    if not explicit:
        if old_areas:
            desired_files = [
                SwapFile(
                    str(area["name"]),
                    str(area["source"]),
                    str(area["size"]),
                    int(area["priority"]),
                )
                for area in old_areas
                if area.get("type") == "file"
            ]
            desired_devices = [
                SwapDevice(
                    str(area["name"]),
                    str(area["source"]),
                    int(area["priority"]),
                    str(area.get("discard", "off")),
                )
                for area in old_areas
                if area.get("type") == "device"
            ]
            desired_zram = [
                SwapZram(
                    str(area["name"]),
                    str(area["size"]),
                    int(area["priority"]),
                    str(area.get("algorithm", "auto")),
                )
                for area in old_areas
                if area.get("type") == "zram"
            ]
        if not old_areas and active:
            print("  ✓ Existing swap is already configured; leaving it unmanaged")
            _configure_swappiness(config.swappiness)
            _configure_zswap(config.zswap, config.zswap_max_pool_percent)
            if config.swap_resume == "":
                _configure_resume(None, [])
            return
        if not old_areas and os.path.exists("/swapfile"):
            print("  ✓ Existing /swapfile is not owned by basaltwater; leaving it unchanged")
            _configure_swappiness(config.swappiness)
            _configure_zswap(config.zswap, config.zswap_max_pool_percent)
            if config.swap_resume == "":
                _configure_resume(None, [])
            return
        if not old_areas:
            automatic = _automatic_swap_file()
            if automatic is None:
                return
            desired_files = [automatic]

    new_areas: list[dict[str, Any]] = []
    for area in desired_files:
        prior = _prior_resource(old_areas, "file", area.path)
        if prior is None and not os.path.exists(area.path):
            pending = {
                "name": area.name,
                "type": "file",
                "source": area.path,
                "priority": area.priority,
                "size": area.size,
                "pending": True,
            }
            _write_state(_ownership_union([*new_areas, pending], old_areas))
            prior = pending
        reconciled = _ensure_swap_file(area, prior)
        new_areas.append(reconciled)
        _write_state(_ownership_union(new_areas, old_areas))
    for area in desired_devices:
        prior = _prior_resource(old_areas, "device", area.source)
        reconciled = _ensure_swap_device(
            config,
            area,
            prior,
            old_areas,
            list(new_areas),
        )
        new_areas.append(reconciled)
        _write_state(_ownership_union(new_areas, old_areas))
    remaining_old = list(old_areas)
    for area in old_areas:
        if area.get("type") == "zram" or any(
            _same_resource(area, item) for item in new_areas
        ):
            continue
        _remove_area(area, active)
        remaining_old.remove(area)
        _write_state(_ownership_union(new_areas, remaining_old))
    old_zram_areas = [area for area in old_areas if area.get("type") == "zram"]
    new_areas.extend(
        _reconcile_zram(
            desired_zram,
            old_zram_areas,
            journal_ownership=lambda records: _write_state(
                _ownership_union([*new_areas, *records], remaining_old)
            ),
        )
    )
    _write_state(new_areas)
    _replace_fstab(
        [entry for area in new_areas if (entry := _fstab_entry(area)) is not None]
    )
    _configure_swappiness(config.swappiness if config.swappiness is not None else 10)
    _configure_zswap(config.zswap, config.zswap_max_pool_percent)
    _configure_resume(config.swap_resume, new_areas)
    _write_state(new_areas)
    print(f"  ✓ Reconciled {len(new_areas)} managed swap area(s)")
