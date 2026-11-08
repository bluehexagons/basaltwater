"""Bounded local mount observations and ext4 error history, without device probes."""

from __future__ import annotations

from datetime import datetime, timezone
import html
import os
import re
import stat

from common.service_tools.web_panel_templates import render_heading
from lib.validation import validate_filesystem_path

MOUNTINFO = "/proc/1/mountinfo"
EXT4_SYSFS = "/sys/fs/ext4"
MAX_MOUNTS = 64
MAX_EXT4 = 64
MAX_BYTES = 1024 * 1024
LOCAL_TYPES = {"ext2", "ext3", "ext4", "xfs", "btrfs", "zfs", "f2fs", "bcachefs",
               "jfs", "reiserfs", "vfat", "exfat", "ntfs", "ntfs3", "iso9660", "udf"}
EXT4_FIELDS = {"errors_count": 2**32 - 1, "first_error_time": 2**40 - 1, "last_error_time": 2**40 - 1}
DEVICE_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.!+\-]{0,127}\Z")
DEVICE_ID = re.compile(r"[0-9]{1,10}:[0-9]{1,10}\Z")
ESCAPES = {"040": " ", "011": "\t", "012": "\n", "134": "\\"}


def _read(path: str, limit: int) -> str:
    validate_filesystem_path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("Expected kernel metadata file")
        with os.fdopen(fd, "rb") as stream:
            fd = -1
            raw = stream.read(limit + 1)
        if len(raw) > limit:
            raise ValueError("Filesystem metadata exceeds limit")
        return raw.decode("utf-8")
    finally:
        if fd >= 0:
            os.close(fd)


def _text(value: object, limit: int) -> str:
    if not isinstance(value, str) or not value or len(value) > limit or not value.isprintable():
        raise ValueError("Invalid filesystem text")
    return value


def _unescape(value: str) -> str:
    # Decode once: a literal backslash followed by 040 must stay literal.
    return re.sub(r"\\([0-7]{3})", lambda match: ESCAPES.get(match[1], match[0]), value)


def _mounts() -> tuple[list[dict], int]:
    rows = []
    for line in _read(MOUNTINFO, MAX_BYTES).splitlines():
        before, after = line.split(" - ", 1)
        fields, filesystem = before.split(), after.split()
        if len(fields) < 6 or len(filesystem) != 3:
            raise ValueError("Invalid mount record")
        if filesystem[0] not in LOCAL_TYPES:
            continue
        if not DEVICE_ID.fullmatch(fields[2]):
            raise ValueError("Invalid mount device")
        target = _text(_unescape(fields[4]), 4096)
        validate_filesystem_path(target)
        if not os.path.isabs(target):
            raise ValueError("Invalid mount target")
        mount_mode = set(fields[5].split(",")) & {"ro", "rw"}
        super_mode = set(filesystem[2].split(",")) & {"ro", "rw"}
        if len(mount_mode) != 1 or len(super_mode) != 1:
            raise ValueError("Invalid mount access mode")
        rows.append({"target": target, "source": _text(_unescape(filesystem[1]), 1024),
                     "fstype": filesystem[0], "device": fields[2],
                     "read_only": "ro" in mount_mode or "ro" in super_mode})
    rows.sort(key=lambda row: (row["target"] != "/", row["target"], row["device"]))
    return rows[:MAX_MOUNTS], max(0, len(rows) - MAX_MOUNTS)


def _ext4() -> list[dict]:
    names = []
    try:
        with os.scandir(EXT4_SYSFS) as entries:
            for entry in entries:
                if entry.name == "features":
                    continue
                if (len(names) == MAX_EXT4 or not DEVICE_NAME.fullmatch(entry.name)
                        or not entry.is_dir(follow_symlinks=False)):
                    raise ValueError("Unsupported ext4 inventory")
                names.append(entry.name)
    except FileNotFoundError:
        return []
    rows = []
    for device in sorted(names):
        row = {"device": device}
        for field, maximum in EXT4_FIELDS.items():
            number = None
            try:
                raw = _read(os.path.join(EXT4_SYSFS, device, field), 32).strip()
                if not re.fullmatch(r"[0-9]{1,13}", raw) or int(raw) > maximum:
                    raise ValueError("Invalid ext4 counter")
                number = int(raw)
            except (OSError, ValueError, UnicodeError):
                pass
            row[field] = number
        rows.append(row)
    return rows


def collect_filesystem_snapshot() -> dict:
    """Read PID 1's mount table and sysfs only; never stat mount paths or run tools."""
    value = {"available": False, "mounts": [], "omitted_mounts": 0, "ext4_available": False, "ext4": []}
    try:
        value["mounts"], value["omitted_mounts"] = _mounts()
        value["available"] = True
    except (OSError, ValueError, UnicodeError):
        pass
    try:
        value["ext4"] = _ext4()
        value["ext4_available"] = True
    except (OSError, ValueError, UnicodeError):
        pass
    return value


def validate_filesystem_snapshot(value: object) -> dict:
    if (not isinstance(value, dict) or set(value) != {"available", "mounts", "omitted_mounts", "ext4_available", "ext4"}
            or any(type(value[key]) is not bool for key in ("available", "ext4_available"))
            or type(value["omitted_mounts"]) is not int or not 0 <= value["omitted_mounts"] <= MAX_BYTES
            or not isinstance(value["mounts"], list) or len(value["mounts"]) > MAX_MOUNTS
            or not isinstance(value["ext4"], list) or len(value["ext4"]) > MAX_EXT4):
        raise ValueError("Invalid filesystem snapshot")
    for row in value["mounts"]:
        if (not isinstance(row, dict) or set(row) != {"target", "source", "fstype", "device", "read_only"}
                or row["fstype"] not in LOCAL_TYPES or type(row["read_only"]) is not bool
                or not DEVICE_ID.fullmatch(_text(row["device"], 21))):
            raise ValueError("Invalid filesystem mount")
        target = _text(row["target"], 4096)
        validate_filesystem_path(target)
        if not os.path.isabs(target):
            raise ValueError("Invalid filesystem target")
        _text(row["source"], 1024)
    seen = set()
    for row in value["ext4"]:
        if (not isinstance(row, dict) or set(row) != {"device", *EXT4_FIELDS}
                or not DEVICE_NAME.fullmatch(_text(row["device"], 128)) or row["device"] in seen):
            raise ValueError("Invalid ext4 device")
        seen.add(row["device"])
        if any(row[key] is not None and (type(row[key]) is not int or not 0 <= row[key] <= maximum)
               for key, maximum in EXT4_FIELDS.items()):
            raise ValueError("Invalid ext4 error history")
    return value


def _time(value: int | None) -> str:
    if value is None:
        return "Unavailable"
    if value == 0:
        return "Not recorded"
    try:
        return datetime.fromtimestamp(value, timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    except (ValueError, OverflowError, OSError):
        return f"Unix time {value}"


def render_filesystem_snapshot(value: dict | None) -> str:
    body = '<section aria-labelledby="filesystem-observations">' + render_heading("Linux filesystems", "storage", heading_id="filesystem-observations")
    try:
        data = validate_filesystem_snapshot(value)
    except (ValueError, TypeError):
        return body + '<p class="empty">Filesystem observations unavailable. Check or update the snapshot exporter.</p></section>'
    body += '<p>Exported mount types and access modes. These observations do not verify file contents. See <a href="/">Mounted storage on the dashboard</a> for space and inode usage.</p>'
    if not data["available"]:
        body += '<p class="empty">Host mount inventory unavailable.</p>'
    elif not data["mounts"]:
        body += '<p>No supported local filesystem mounts were observed.</p>'
    if data["mounts"]:
        body += '<details><summary>Local mounts and access modes</summary><ul class="data-list">'
        for row in data["mounts"]:
            mode = '<span class="badge warning">Read-only mount</span>' if row["read_only"] else 'Read/write mount'
            body += (f'<li class="event"><h3><code>{html.escape(row["target"])}</code></h3>'
                     f'<p>{html.escape(row["fstype"])} · {html.escape(row["source"])} · Device {row["device"]} · {mode}</p></li>')
        body += '</ul></details>'
    if data["omitted_mounts"]:
        body += f'<p>{data["omitted_mounts"]} additional local mounts omitted; inspect the host mount table for the complete inventory.</p>'
    body += '<p class="data-notice">Read-only mounts may be intentional. An unexpected change needs investigation in the kernel journal. Bind mounts can repeat the same filesystem. Network, FUSE and virtual mounts are excluded; this view does not probe mount paths.</p>'
    body += '<h3>ext4 retained error history</h3>'
    if not data["ext4_available"]:
        body += '<p class="empty">ext4 kernel inventory unavailable.</p>'
    elif not data["ext4"]:
        body += '<p>No ext4 driver devices were observed in the kernel inventory.</p>'
    if data["ext4"]:
        body += '<p>Match kernel device names and mount device numbers with <code>lsblk -o NAME,MAJ:MIN,FSTYPE,MOUNTPOINTS</code> on the host. Device-mapper aliases can differ from kernel names.</p>'
    for row in data["ext4"]:
        count = row["errors_count"]
        status = ('Error count unavailable' if count is None else
                  '<span class="badge warning">Recorded filesystem errors</span>' if count else 'No retained errors reported')
        body += (f'<article class="event"><h4>{html.escape(row["device"])}</h4><p>{status}</p><dl class="job-facts">'
                 f'<div><dt>Retained error count</dt><dd>{count if count is not None else "Unavailable"}</dd></div>'
                 f'<div><dt>First recorded error</dt><dd>{_time(row["first_error_time"])}</dd></div>'
                 f'<div><dt>Last recorded error</dt><dd>{_time(row["last_error_time"])}</dd></div></dl></article>')
    body += '<p>ext4 counters retain filesystem error history; zero does not prove current health. Missing or unsupported fields are unavailable. Review nonzero counts alongside the kernel journal and backups before planning maintenance.</p>'
    types = {row["fstype"] for row in data["mounts"]}
    if types & {"ext2", "ext3", "ext4"} or data["ext4"]:
        body += '<p><strong>ext2/ext3/ext4:</strong> Plan <code>e2fsck</code> checks with the filesystem unmounted, using rescue mode for the root filesystem. Mounted check results are unreliable.</p>'
    if "xfs" in types:
        body += '<p><strong>XFS:</strong> An administrator can use <code>xfs_scrub -n MOUNTPOINT</code> to check mounted filesystem metadata without repairs or optimizations, where supported. This does not verify file contents against known checksums. Run <code>xfs_repair</code> only with the filesystem unmounted.</p>'
    body += '<p>Filesystem checks and PAR2 file checks cover different evidence. This panel starts no native filesystem checks or repairs and changes no mount settings.</p>'
    return body + '<a class="refresh-link" href="https://github.com/bluehexagons/basaltwater/blob/main/docs/services/shares-data.md#inspect-other-linux-filesystems">Filesystem inspection guide →</a></section>'
