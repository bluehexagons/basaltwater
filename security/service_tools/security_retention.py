"""Bounded evidence for the beginning of retained security source history."""

from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime, timedelta

from lib.streamed_process import CommandOutputLimitError, run_captured, run_streamed
from lib.types import JSONDict

# Four probes must leave room for event-query deadlines and error reporting
# within the monitor's ten-minute service limit.
_TIMEOUT = 15
_AUDIT_RECORD_BYTES = 256 * 1024
_JOURNAL_COMMAND = (
    'journalctl', '--system', '--no-pager', '--quiet', '--no-tail',
    '--output=json', '--output-fields=__REALTIME_TIMESTAMP',
)
_SOURCES = {'auditd', 'ssh_journal'}


class _FirstJournalRecord(Exception):
    """Stop the supervised reader after the oldest complete journal record."""

    def __init__(self, timestamp: datetime) -> None:
        self.timestamp = timestamp
        super().__init__('Oldest journal record received')


def _oldest_journal() -> datetime:
    """Read one global system record, including on systemd without -n +1.

    Do not filter by SSH: a quiet service's first event is not a retention
    boundary. run_streamed terminates and reaps its isolated process group
    when the callback stops reading; it never buffers the entire journal.
    """
    def consume(line: str) -> None:
        if not line.strip():
            return
        if len(line.encode('utf-8')) > 64 * 1024:
            raise ValueError('Oversized journal metadata')
        record = json.loads(line)
        raw = record.get('__REALTIME_TIMESTAMP') if isinstance(record, dict) else None
        if not isinstance(raw, str) or not re.fullmatch(r'\d{1,20}', raw):
            raise ValueError('Missing journal timestamp')
        # The monitor queries complete seconds; avoid floating-point rounding.
        raise _FirstJournalRecord(datetime.fromtimestamp(int(raw) // 1_000_000))

    def reject_error(line: str) -> None:
        if line.strip():
            raise ValueError('Journal retention query reported an error')

    try:
        run_streamed(list(_JOURNAL_COMMAND), timeout=_TIMEOUT,
                     on_output=consume, on_error=reject_error)
    except _FirstJournalRecord as first:
        return first.timestamp
    raise ValueError('No retained journal record')


def _oldest_audit(ausearch: str) -> datetime:
    """Use the same configured/rotated audit logs as the event queries."""
    result = run_captured(
        [ausearch, '--input-logs', '--raw', '--just-one'],
        timeout=_TIMEOUT, max_bytes=_AUDIT_RECORD_BYTES,
    )
    if result.returncode != 0 or result.stderr.strip():
        raise ValueError('Audit retention query failed or returned no records')
    # Only record headers establish time. Audit arguments may themselves
    # contain strings such as msg=audit(0:1), which are not source metadata.
    timestamps = re.findall(
        r'^(?:node=\S+\s+)?type=\S+\s+msg=audit\((\d{1,16})(?:\.\d+)?:\d+\):',
        result.stdout, re.MULTILINE,
    )
    if not timestamps:
        raise ValueError('No retained audit timestamp')
    return datetime.fromtimestamp(min(int(value) for value in timestamps))


def inspect_retention(ausearch: str | None) -> tuple[dict[str, datetime], list[str]]:
    """Return known global source floors and fail closed on unknown evidence."""
    floors: dict[str, datetime] = {}
    errors: list[str] = []
    probes = [('ssh_journal', 'SSH journal', _oldest_journal)]
    if ausearch:
        probes.append(('auditd', 'auditd', lambda: _oldest_audit(ausearch)))
    for source, label, probe in probes:
        try:
            floors[source] = probe()
        except (OSError, ValueError, OverflowError, TimeoutError,
                subprocess.TimeoutExpired, CommandOutputLimitError):
            errors.append(f'{label}: retention evidence unavailable; interval coverage is unknown')
    return floors, errors


def window_gaps(
    floors: dict[str, datetime], since: datetime, until: datetime,
) -> list[JSONDict]:
    """Report uncovered prefixes with an exclusive end, never infer lost events."""
    return [
        {'source': source, 'since': since.isoformat(),
         'until': min(oldest, until + timedelta(seconds=1)).isoformat()}
        for source, oldest in sorted(floors.items()) if oldest > since
    ]


def retained_gaps(state: dict[str, object], gaps: list[JSONDict]) -> list[JSONDict]:
    """Keep the latest contiguous uncovered interval per source, bounded to two."""
    latest: dict[str, JSONDict] = {}
    previous = state.get('retention_gaps')
    for record in [*(previous if isinstance(previous, list) else []), *gaps]:
        if not isinstance(record, dict):
            continue
        source, since, until = (record.get(key) for key in ('source', 'since', 'until'))
        if not isinstance(source, str) or source not in _SOURCES:
            continue
        if not all(isinstance(value, str) and len(value) <= 32 for value in (since, until)):
            continue
        try:
            start, end = datetime.fromisoformat(since), datetime.fromisoformat(until)
            if start.tzinfo is not None or end.tzinfo is not None or start >= end:
                continue
        except ValueError:
            continue
        old = latest.get(source)
        if old and str(old['since']) <= since <= str(old['until']):
            since = str(old['since'])
            until = max(until, str(old['until']))
        latest[source] = {'source': source, 'since': since, 'until': until}
    return [latest[source] for source in sorted(latest)]
