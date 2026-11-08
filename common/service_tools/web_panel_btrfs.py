"""Bounded Btrfs kernel counters and retained native scrub metadata; no ioctls."""

from __future__ import annotations

import html
import os
import re
import stat

from common.service_tools.web_panel_templates import render_heading
from lib.validation import validate_filesystem_path

SYSFS = "/sys/fs/btrfs"
SCRUB_STATE = "/var/lib/btrfs"
MAX_FILESYSTEMS = 16
MAX_DEVICES = 64
UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")
COUNTERS = ("write_errs", "read_errs", "flush_errs", "corruption_errs", "generation_errs")
SCRUB_COUNTERS = ("corrected_errors", "uncorrectable_errors", "unverified_errors")
SCRUB_ERRORS = {"read_errors": "Read errors", "csum_errors": "Checksum errors",
                "verify_errors": "Header verification errors", "super_errors": "Superblock errors",
                "malloc_errors": "Memory allocation errors"}


def _read(path: str, limit: int = 65536) -> str:
    validate_filesystem_path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("Expected Btrfs metadata file")
        with os.fdopen(fd, "rb") as stream:
            fd = -1
            value = stream.read(limit + 1)
        if len(value) > limit:
            raise ValueError("Btrfs metadata exceeds limit")
        return value.decode("ascii")
    finally:
        if fd >= 0:
            os.close(fd)


def _entries(path: str, limit: int) -> list[str]:
    names = []
    with os.scandir(path) as entries:
        for entry in entries:
            if len(names) == limit:
                raise ValueError("Btrfs inventory exceeds limit")
            if not entry.is_dir(follow_symlinks=False):
                raise ValueError("Unexpected Btrfs inventory entry")
            names.append(entry.name)
    return sorted(names)


def _numbers(text: str, separator: str | None = None) -> dict[str, int]:
    values = {}
    for field in text.split("|") if separator else text.splitlines():
        pair = field.split(separator) if separator else field.split()
        if (len(pair) != 2 or pair[0] in values or not re.fullmatch(r"[0-9]{1,20}", pair[1])
                or int(pair[1]) > 2**64 - 1):
            raise ValueError("Invalid Btrfs counters")
        values[pair[0]] = int(pair[1])
    return values


def _scrub(fsid: str, devices: list[str]) -> dict:
    try:
        text = _read(os.path.join(SCRUB_STATE, "scrub.status." + fsid))
    except FileNotFoundError:
        return {"state": "not_recorded"}
    lines = text.splitlines()
    if not lines or lines[0] != "scrub status:1" or not 1 <= len(lines) - 1 <= MAX_DEVICES:
        raise ValueError("Unsupported native scrub record")
    rows = {}
    for line in lines[1:]:
        identity, fields = line.split("|", 1)
        uuid, device = identity.split(":")
        if uuid != fsid or device not in devices or device in rows:
            raise ValueError("Mismatched native scrub record")
        row = _numbers(fields, ":")
        # btrfs-progs' v1 retained writer omits unverified_errors, even though
        # the live ioctl and CLI can report it. Do not infer zero from absence.
        if (not all(key in row for key in ("corrected_errors", "uncorrectable_errors", "finished", "canceled", "t_start"))
                or not row["t_start"] or row["finished"] not in {0, 1} or row["canceled"] not in {0, 1}):
            raise ValueError("Incomplete native scrub record")
        rows[device] = row
    state = ("cancelled" if any(row["canceled"] for row in rows.values()) else
             "completed" if all(row["finished"] for row in rows.values()) else "unfinished")

    def total(key: str) -> int | None:
        return sum(row[key] for row in rows.values()) if all(key in row for row in rows.values()) else None

    return {"state": state, "devices_recorded": len(rows),
            **{key: total(key) for key in SCRUB_COUNTERS},
            "errors": {key: total(key) for key in SCRUB_ERRORS}}


def collect_btrfs() -> dict:
    """Observe mounted filesystem sysfs entries, including hosts without PAR2."""
    result = {"available": True, "filesystems": []}
    try:
        names = _entries(SYSFS, MAX_FILESYSTEMS + 8)
        fsids = [name for name in names if UUID.fullmatch(name)]
        if len(fsids) > MAX_FILESYSTEMS:
            raise ValueError("Too many Btrfs filesystems")
        for fsid in fsids:
            row = {"uuid": fsid, "devices": [], "scrub": {"state": "unavailable"}}
            result["filesystems"].append(row)
            try:
                devices = _entries(os.path.join(SYSFS, fsid, "devinfo"), MAX_DEVICES)
                if not devices or any(not re.fullmatch(r"[1-9][0-9]{0,19}", device) or int(device) > 2**64 - 1 for device in devices):
                    raise ValueError("Invalid Btrfs devices")
                for device in devices:
                    counters = None
                    try:
                        counters = _numbers(_read(os.path.join(SYSFS, fsid, "devinfo", device, "error_stats")))
                        if set(counters) != set(COUNTERS):
                            raise ValueError("Unsupported device counters")
                    except (OSError, ValueError, UnicodeError):
                        counters = None
                    row["devices"].append({"id": device, "counters": counters})
                row["scrub"] = _scrub(fsid, devices)
            except (OSError, ValueError, UnicodeError):
                pass
    except FileNotFoundError:
        # A missing Btrfs sysfs root means the kernel has no Btrfs inventory.
        if os.path.exists(SYSFS):
            result["available"] = False
    except (OSError, ValueError):
        result["available"] = False
    return result


def validate_btrfs(value: object) -> dict:
    if not isinstance(value, dict) or set(value) != {"available", "filesystems"} or type(value["available"]) is not bool:
        raise ValueError("Invalid Btrfs snapshot")
    if not isinstance(value["filesystems"], list) or len(value["filesystems"]) > MAX_FILESYSTEMS:
        raise ValueError("Invalid Btrfs inventory")
    seen = set()
    for row in value["filesystems"]:
        if (not isinstance(row, dict) or set(row) != {"uuid", "devices", "scrub"}
                or not isinstance(row["uuid"], str) or not UUID.fullmatch(row["uuid"]) or row["uuid"] in seen
                or not isinstance(row["devices"], list) or len(row["devices"]) > MAX_DEVICES):
            raise ValueError("Invalid Btrfs filesystem")
        seen.add(row["uuid"])
        device_ids = set()
        for device in row["devices"]:
            if (not isinstance(device, dict) or set(device) != {"id", "counters"}
                    or not isinstance(device["id"], str) or not re.fullmatch(r"[1-9][0-9]{0,19}", device["id"])
                    or int(device["id"]) > 2**64 - 1
                    or device["id"] in device_ids):
                raise ValueError("Invalid Btrfs device")
            device_ids.add(device["id"])
            counters = device["counters"]
            if counters is not None and (not isinstance(counters, dict) or set(counters) != set(COUNTERS)
                    or any(type(n) is not int or not 0 <= n <= 2**64 - 1 for n in counters.values())):
                raise ValueError("Invalid Btrfs device counters")
        scrub = row["scrub"]
        if not isinstance(scrub, dict) or scrub.get("state") not in {"not_recorded", "unavailable", "completed", "cancelled", "unfinished"}:
            raise ValueError("Invalid native scrub state")
        keys = {"state"} if scrub["state"] in {"not_recorded", "unavailable"} else {"state", "devices_recorded", *SCRUB_COUNTERS}
        if "errors" in scrub and "devices_recorded" in keys:
            keys.add("errors")
        if set(scrub) != keys:
            raise ValueError("Invalid native scrub counters")
        if "devices_recorded" in scrub:
            if type(scrub["devices_recorded"]) is not int or not 1 <= scrub["devices_recorded"] <= len(device_ids):
                raise ValueError("Invalid native scrub coverage")
            for key in SCRUB_COUNTERS:
                n = scrub[key]
                if key == "unverified_errors" and n is None:
                    continue
                if type(n) is not int or not 0 <= n <= scrub["devices_recorded"] * (2**64 - 1):
                    raise ValueError("Invalid native scrub counters")
        if "errors" in scrub:
            errors = scrub["errors"]
            if (not isinstance(errors, dict) or set(errors) != set(SCRUB_ERRORS)
                    or any(n is not None and (type(n) is not int or not 0 <= n <= scrub["devices_recorded"] * (2**64 - 1))
                           for n in errors.values())):
                raise ValueError("Invalid native scrub error details")
    return value


def render_btrfs(value: dict | None) -> str:
    body = '<section aria-labelledby="btrfs-health">' + render_heading("Btrfs filesystem health", "storage", heading_id="btrfs-health")
    try:
        data = validate_btrfs(value)
    except (ValueError, TypeError):
        data = {"available": False, "filesystems": []}
    body += '<p>Native filesystem checks are separate from PAR2 file checks. This view reads exported metadata; it does not start or schedule a native scrub.</p>'
    if not data["available"]:
        body += '<p class="empty">Btrfs inventory unavailable. Check or update the snapshot exporter.</p>'
    elif not data["filesystems"]:
        return body + '<p>No mounted Btrfs filesystem was observed in the kernel inventory.</p></section>'
    if data["filesystems"]:
        body += '<p>Match filesystem UUIDs to mount points with <code>findmnt -t btrfs -o TARGET,UUID</code> on the host.</p>'
    for row in data["filesystems"]:
        body += '<article class="event"><h3>Filesystem ' + html.escape(row["uuid"]) + '</h3>'
        if not row["devices"]:
            body += '<p>Device counters unavailable.</p>'
        for device in row["devices"]:
            counters = device["counters"]
            body += '<p><strong>Device ' + device["id"] + '</strong>: '
            if counters is None:
                body += 'Counters unavailable.</p>'
            else:
                body += ('<span class="badge warning">Recorded device errors</span>' if any(counters.values()) else 'No retained device errors reported') + '</p><dl class="job-facts">'
                for key, label in zip(COUNTERS, ("Write errors", "Read errors", "Flush errors", "Corruption errors", "Generation errors")):
                    body += f'<div><dt>{label}</dt><dd>{counters[key]}</dd></div>'
                body += '</dl>'
        scrub = row["scrub"]
        labels = {"not_recorded": "No native scrub record", "unavailable": "Native scrub record unavailable",
                  "completed": "Recorded completion for selected devices", "cancelled": "Recorded scrub cancelled",
                  "unfinished": "Unfinished record; current activity unknown"}
        body += '<p><strong>' + labels[scrub["state"]] + '</strong></p>'
        if "devices_recorded" in scrub:
            if scrub["uncorrectable_errors"]:
                body += '<p><span class="badge warning">Native scrub recorded unresolved errors</span></p>'
            if scrub["unverified_errors"]:
                body += '<p><span class="badge warning">Recorded intermittent read errors</span></p>'
            unverified = scrub["unverified_errors"] if scrub["unverified_errors"] is not None else "Unavailable"
            body += f'<p>Devices in record: {scrub["devices_recorded"]} of {len(row["devices"])}. Corrected: {scrub["corrected_errors"]} · Uncorrectable: {scrub["uncorrectable_errors"]} · Unverified: {unverified}.</p>'
            if unverified == "Unavailable":
                body += '<p>Retained status files may omit unverified errors; absence is not zero.</p>'
            errors = scrub.get("errors")
            if errors:
                if errors["malloc_errors"]:
                    body += '<p class="badge warning">Native scrub recorded memory allocation failures; results may be incomplete.</p>'
                if errors["verify_errors"] or errors["super_errors"]:
                    body += '<p class="badge warning">Native scrub recorded metadata errors.</p>'
                body += '<details><summary>Native scrub error details</summary><dl class="job-facts">'
                for key, label in SCRUB_ERRORS.items():
                    body += f'<div><dt>{label}</dt><dd>{errors[key] if errors[key] is not None else "Unavailable"}</dd></div>'
                body += '</dl></details>'
        body += '</article>'
    return body + ('<p>Device counters retain historical errors. Zero counters or a completed record do not prove current health. '
        'Records can cover only selected devices and do not confirm a currently running scrub. '
        'Native scrub needs a good replica to repair damage; single-copy data has no replica. Files with NODATASUM are not data-checksummed. '
        'Snapshots and reflinks share blocks and are not independent backups.</p>'
        '<a class="refresh-link" href="https://github.com/bluehexagons/basaltwater/blob/main/docs/storage/storage-operations.md#btrfs-filesystem-scrub">Inspect or run a native Btrfs scrub →</a></section>')
