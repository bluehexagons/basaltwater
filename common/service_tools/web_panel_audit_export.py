#!/usr/bin/env python3
"""Export a sanitized, bounded auditd snapshot for the web panel."""

from __future__ import annotations

import argparse
import os
import re
import resource
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from typing import Any


SOURCE_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
)
if SOURCE_ROOT not in sys.path:
    sys.path.insert(0, SOURCE_ROOT)

from common.web_panel_events import WEB_PANEL_AUDIT_SNAPSHOT
from lib.atomic_io import write_json_atomic
from lib.security_activity import managed_setup_audit_window
from lib.types import JSONDict
from lib.validation import validate_filesystem_path


_AUDIT_KEYS = ("identity", "sudoers", "sshd_config", "modules", "privileged")
_CRITICAL_KEYS = frozenset(("identity", "sudoers", "sshd_config", "modules"))
_KEY_LABELS = {
    "identity": "Account database changed",
    "sudoers": "Administrator access policy changed",
    "sshd_config": "SSH server configuration changed",
    "modules": "Kernel module activity",
    "privileged": "Privileged command execution",
}
_MAX_COMMAND_OUTPUT_BYTES = 2 * 1024 * 1024
_MAX_EVENTS = 100
_MAX_PRIVILEGED_EVENTS = 25
_MAX_VALUES = 10


def _limit_output_file_size() -> None:
    resource.setrlimit(
        resource.RLIMIT_FSIZE,
        (_MAX_COMMAND_OUTPUT_BYTES + 1, _MAX_COMMAND_OUTPUT_BYTES + 1),
    )


def _run_bounded(command: list[str], *, timeout: int) -> tuple[int, str] | None:
    """Run a local audit command without allowing unbounded captured output."""

    try:
        with tempfile.TemporaryFile() as output_file:
            result = subprocess.run(
                command,
                check=False,
                stdout=output_file,
                stderr=subprocess.DEVNULL,
                timeout=timeout,
                preexec_fn=_limit_output_file_size,
            )
            output_size = output_file.tell()
            output_file.seek(0)
            output = output_file.read(_MAX_COMMAND_OUTPUT_BYTES + 1).decode(
                "utf-8", errors="replace"
            )
    except (OSError, subprocess.SubprocessError):
        return None
    if output_size > _MAX_COMMAND_OUTPUT_BYTES:
        return None
    return result.returncode, output


def _audit_health() -> tuple[str, list[str]]:
    """Verify that auditd and every rule used by the panel are active."""

    if not shutil.which("auditctl"):
        return (
            "unavailable",
            ["auditctl is unavailable, so kernel audit coverage cannot be verified."],
        )
    status_result = _run_bounded(["auditctl", "-s"], timeout=10)
    if status_result is None or status_result[0] != 0:
        return (
            "unavailable",
            ["The kernel audit status could not be read."],
        )
    status_values = {
        match.group(1): match.group(2)
        for line in status_result[1].splitlines()
        if (match := re.match(r"^([a-z_]+)\s+(\S+)$", line.strip()))
    }
    if status_values.get("enabled") not in {"1", "2"}:
        return "unavailable", ["Kernel auditing is disabled."]
    try:
        daemon_pid = int(status_values.get("pid", "0"))
    except ValueError:
        daemon_pid = 0
    if daemon_pid <= 0:
        return "unavailable", ["auditd is not running."]

    rules_result = _run_bounded(["auditctl", "-l"], timeout=10)
    if rules_result is None or rules_result[0] != 0:
        return "degraded", ["The loaded audit rules could not be verified."]
    loaded_keys = set(
        re.findall(
            r"(?:^|\s)(?:-k\s+|-F\s+key=)([A-Za-z0-9_.:-]+)",
            rules_result[1],
        )
    )
    missing_keys = [key for key in _AUDIT_KEYS if key not in loaded_keys]
    if missing_keys:
        return (
            "degraded",
            ["Expected audit rules are not loaded: " + ", ".join(missing_keys) + "."],
        )
    return "ok", []


def _safe_values(record: str, field_name: str) -> list[str]:
    pattern = re.compile(rf'\b{re.escape(field_name)}=(?:"([^"]*)"|(\S+))')
    values: list[str] = []
    for match in pattern.finditer(record):
        value = (match.group(1) or match.group(2) or "").strip()
        if (
            value
            and len(value) <= 512
            and not any(ord(character) < 32 for character in value)
            and value not in values
        ):
            values.append(value)
        if len(values) >= _MAX_VALUES:
            break
    return values


def _record_timestamp(record: str) -> datetime | None:
    match = re.search(r"\bmsg=audit\((\d+(?:\.\d+)?):", record)
    if match:
        try:
            return datetime.fromtimestamp(float(match.group(1)), timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None
    interpreted = re.search(
        r"\bmsg=audit\((\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2})(?:\.\d+)?:\d+\)",
        record,
    )
    if not interpreted:
        return None
    try:
        local_time = datetime.strptime(
            interpreted.group(1), "%m/%d/%Y %H:%M:%S"
        ).astimezone()
    except ValueError:
        return None
    return local_time.astimezone(timezone.utc)


def _local_naive(value: datetime) -> datetime:
    """Return a datetime in the host timezone without timezone metadata."""

    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=None)
    return value.astimezone().replace(tzinfo=None)


def _timestamp_in_window(
    timestamp: datetime,
    window: tuple[datetime, datetime] | None,
) -> bool:
    """Return whether an audit event occurred during managed setup."""

    if window is None:
        return False
    local_timestamp = _local_naive(timestamp)
    return window[0] <= local_timestamp <= window[1]


def _parse_record(key: str, record: str) -> JSONDict | None:
    timestamp = _record_timestamp(record)
    if timestamp is None:
        return None
    event: JSONDict = {
        "key": key,
        "meaning": _KEY_LABELS[key],
        "severity": "warning" if key in _CRITICAL_KEYS else "info",
        "timestamp": timestamp.isoformat(timespec="seconds"),
    }
    for output_name, field_name in (
        ("paths", "name"),
        ("operations", "syscall"),
        ("executables", "exe"),
    ):
        values = _safe_values(record, field_name)
        if values:
            event[output_name] = values
    actors: list[str] = []
    for field_name in ("acct", "auid", "uid"):
        for value in _safe_values(record, field_name):
            if value not in actors:
                actors.append(value)
            if len(actors) >= _MAX_VALUES:
                break
    if actors:
        event["actors"] = actors
    return event


def collect_audit_snapshot(*, now: datetime | None = None) -> dict[str, Any]:
    """Collect the latest day of configured audit events without raw records."""

    generated = now or datetime.now(timezone.utc)
    snapshot: dict[str, Any] = {
        "version": 1,
        "generated_at": generated.isoformat(timespec="seconds"),
        "status": "ok",
        "issues": [],
        "events": [],
        "suppressed_setup_events": 0,
    }
    if not shutil.which("ausearch"):
        snapshot["status"] = "unavailable"
        snapshot["issues"] = [
            "ausearch is unavailable, so audit events cannot be queried."
        ]
        return snapshot

    health_status, issues = _audit_health()
    snapshot["status"] = health_status
    snapshot["issues"] = issues
    if health_status == "unavailable":
        return snapshot

    local_generated = _local_naive(generated)
    since = local_generated - timedelta(hours=24)
    setup_window = managed_setup_audit_window(since, local_generated)
    suppressed_setup_events = 0
    events: list[JSONDict] = []
    for key in _AUDIT_KEYS:
        query_result = _run_bounded(
            [
                "ausearch",
                "--start",
                since.strftime("%m/%d/%Y"),
                since.strftime("%H:%M:%S"),
                "-k",
                key,
                "-i",
            ],
            timeout=20,
        )
        if query_result is not None and query_result[0] == 1:
            continue
        if query_result is None or query_result[0] != 0:
            snapshot["status"] = "degraded"
            snapshot["issues"].append(
                f'The "{_KEY_LABELS[key]}" event query failed.'
            )
            continue
        output = query_result[1]
        for record in output.split("----"):
            if "type=" not in record:
                continue
            timestamp = _record_timestamp(record)
            if timestamp is None:
                continue
            if _timestamp_in_window(timestamp, setup_window):
                suppressed_setup_events += 1
                continue
            event = _parse_record(key, record)
            if event is not None:
                events.append(event)

    events.sort(key=lambda event: str(event.get("timestamp", "")), reverse=True)
    critical_events = [event for event in events if event.get("key") != "privileged"]
    privileged_events = [
        event for event in events if event.get("key") == "privileged"
    ][:_MAX_PRIVILEGED_EVENTS]
    retained_events = [*critical_events, *privileged_events]
    retained_events.sort(
        key=lambda event: str(event.get("timestamp", "")), reverse=True
    )
    snapshot["events"] = retained_events[:_MAX_EVENTS]
    snapshot["suppressed_setup_events"] = suppressed_setup_events
    return snapshot


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export a sanitized auditd snapshot for the infra-tools web panel"
    )
    parser.add_argument("--output", default=WEB_PANEL_AUDIT_SNAPSHOT)
    return parser


def main() -> int:
    args = _parser().parse_args()
    validate_filesystem_path(args.output, must_exist=False)
    write_json_atomic(
        args.output,
        collect_audit_snapshot(),
        mode=0o640,
        sort_keys=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
