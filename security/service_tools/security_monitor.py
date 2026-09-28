#!/usr/bin/env python3
"""
Security Event Monitor

Checks security logs for notable events and sends notifications via the
configured basaltwater notification targets. Runs every 15 minutes.

Event sources (where installed):
  - fail2ban: ban/unban events from /var/log/fail2ban.log
  - auditd: identity, sudoers, SSH config, module, and privileged-exec events
  - SSH: failed authentication attempts from the system journal
  - XRDP: TLS certificate/key validity, expiry, permissions, and readability

Severity rules:
  error   — XRDP TLS material is unusable, or an event source cannot be read
  warning — fail2ban banned an IP address, auditd found a protected-file or
             module change, or the XRDP certificate expires soon
  info    — SSH auth failures at or above the reporting threshold (default: 5),
             and certificate recovery/rotation

Routine privileged-exec audit hits are retained for context when another event
is reported, but do not create a notification by themselves. Collection
failures notify once when they begin and again when the source recovers.

Logs to: /var/log/basaltwater/security/security_monitor.log
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from logging import ERROR, WARNING

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '../..'))

from lib.logging_utils import get_service_logger, log_event
from lib.atomic_io import write_json_atomic
from lib.notifications import load_notification_configs_from_state, send_notification_safe
from lib.security_activity import managed_setup_audit_window
from lib.streamed_process import run_streamed
from lib.types import JSONDict
from lib.validation import validate_filesystem_path, validate_network_ip
from lib.xrdp_certificate import XrdpCertificateHealth, inspect_xrdp_certificate
from common.service_tools.web_panel_audit_export import _audit_health

logger = get_service_logger('security_monitor', 'security', use_syslog=True)

_STATE_FILE = '/opt/basaltwater/state/security_monitor_state.json'
_FAIL2BAN_LOG = '/var/log/fail2ban.log'
_SSH_FAILURE_THRESHOLD = 5
_SSH_WARNING_THRESHOLD = 25
_SSH_MAX_BREAKDOWN = 10
_SSH_MAX_GROUPS = 1000
_MAX_FAIL2BAN_EVENTS = 50
_MAX_AUDIT_RECORD_BYTES = 256 * 1024
_MAX_SOURCE_LINE_BYTES = 64 * 1024
_SCAN_WINDOW = timedelta(minutes=15)
_SOURCE_TIMEOUT_SECONDS = 60
_AUDIT_PATH = '/usr/sbin:/sbin:/usr/bin:/bin'


def _audit_tool(name: str) -> str | None:
    """Resolve packaged audit commands without depending on the caller PATH."""
    return shutil.which(name, path=_AUDIT_PATH)

# fail2ban log line pattern — local-time timestamp + jail + action + IP.
# Jail names may contain hyphens (e.g. nginx-http-auth), so [^\]]+ is used.
_BAN_RE = re.compile(
    r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+'
    r' fail2ban\.actions\s+\[.*?\]:\s+(?:WARNING|NOTICE)\s+\[([^\]]+)\] (Ban|Unban) (\S+)'
)

# auditd keys that trigger integrity-change notifications.
_CRITICAL_KEYS = ('identity', 'sudoers', 'sshd_config', 'modules')
# auditd keys included in notifications but not used to raise severity —
# 'privileged' fires on every sudo call, which is routine admin activity.
_INFO_KEYS = ('privileged',)
_AUDIT_KEY_LABELS = {
    'identity': 'account database changed',
    'sudoers': 'administrator access policy changed',
    'sshd_config': 'SSH server configuration changed',
    'modules': 'kernel module activity',
    'privileged': 'privileged command execution',
}
_SECURITY_EVENT_SCHEMA_VERSION = 2


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

def _load_state() -> dict[str, object]:
    try:
        with open(_STATE_FILE) as f:
            state = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    return state if isinstance(state, dict) else {}


def _save_state(state: dict[str, object]) -> None:
    """Persist the collection cursor without exposing a partial JSON file."""
    validate_filesystem_path(_STATE_FILE, must_exist=False)
    write_json_atomic(_STATE_FILE, state, mode=0o600, sort_keys=True)


# ---------------------------------------------------------------------------
# fail2ban
# ---------------------------------------------------------------------------

@dataclass
class Fail2banScan:
    """Exact fail2ban totals with a bounded sample for notifications."""

    bans: list[JSONDict] = field(default_factory=list)
    unbans: list[JSONDict] = field(default_factory=list)
    ban_count: int = 0
    unban_count: int = 0
    error: str | None = None


def _check_fail2ban(
    since: datetime, until: datetime
) -> Fail2banScan:
    """Count all ban/unban events while retaining bounded examples."""
    scan = Fail2banScan()
    if not os.path.exists(_FAIL2BAN_LOG):
        # fail2ban is optional on some supported machine types.  Once its
        # client is installed, however, a missing log means this source is no
        # longer observable (including when the service switched log targets).
        if shutil.which('fail2ban-client'):
            scan.error = f'fail2ban: log file unavailable: {_FAIL2BAN_LOG}'
        return scan
    try:
        with open(_FAIL2BAN_LOG, encoding='utf-8') as f:
            while line := f.readline(_MAX_SOURCE_LINE_BYTES + 1):
                if len(line.encode('utf-8')) > _MAX_SOURCE_LINE_BYTES:
                    scan.error = 'fail2ban log: line exceeded the size limit'
                    return scan
                m = _BAN_RE.match(line)
                if not m:
                    continue
                try:
                    ts = datetime.strptime(m.group(1), '%Y-%m-%d %H:%M:%S')
                except ValueError:
                    continue
                if ts < since or ts > until:
                    continue
                jail, action, ip = m.group(2), m.group(3), m.group(4)
                if len(jail) > 128 or not re.fullmatch(r'[A-Za-z0-9_.-]+', jail):
                    jail = 'unknown'
                try:
                    ip = validate_network_ip(ip, 'fail2ban source')
                except ValueError:
                    ip = 'unknown'
                entry: JSONDict = {
                    "type": "fail2ban",
                    "action": action.lower(),
                    "severity": "warning" if action == "Ban" else "info",
                    "timestamp": m.group(1),
                    "jail": jail,
                    "source_ip": ip,
                }
                if action == 'Ban':
                    scan.ban_count += 1
                    if len(scan.bans) < _MAX_FAIL2BAN_EVENTS:
                        scan.bans.append(entry)
                else:
                    scan.unban_count += 1
                    if len(scan.unbans) < _MAX_FAIL2BAN_EVENTS:
                        scan.unbans.append(entry)
    except (OSError, UnicodeError) as exc:
        scan.error = f'fail2ban log: {exc}'
    return scan


# ---------------------------------------------------------------------------
# auditd
# ---------------------------------------------------------------------------

def _audit_field_values(record: str, field_name: str) -> list[str]:
    """Extract simple auditd field values from one ausearch record."""
    pattern = re.compile(rf'\b{re.escape(field_name)}=(?:"([^"]*)"|(\S+))')
    values: list[str] = []
    for match in pattern.finditer(record):
        value = match.group(1) or match.group(2)
        if value and len(value) <= 512 and value not in values:
            values.append(value)
    return values


def _audit_record_timestamp(record: str) -> datetime | None:
    """Return the local timestamp embedded in an audit event record."""
    epoch_match = re.search(r'\bmsg=audit\((\d+(?:\.\d+)?):', record)
    if epoch_match:
        try:
            return datetime.fromtimestamp(float(epoch_match.group(1)))
        except (OSError, OverflowError, ValueError):
            return None
    interpreted_match = re.search(
        r'\bmsg=audit\((\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2})(?:\.\d+)?:\d+\)',
        record,
    )
    if interpreted_match:
        try:
            return datetime.strptime(interpreted_match.group(1), '%m/%d/%Y %H:%M:%S')
        except ValueError:
            return None
    return None


def _parse_audit_events(
    key: str,
    output: str,
    excluded_window: tuple[datetime, datetime] | None = None,
) -> list[JSONDict]:
    """Summarise ausearch records without forwarding raw audit log text."""
    records = [record for record in output.split('----') if 'type=' in record]
    if excluded_window:
        window_start, window_end = excluded_window
        records = [
            record
            for record in records
            if (
                (timestamp := _audit_record_timestamp(record)) is None
                or not window_start <= timestamp <= window_end
            )
        ]
    if not records:
        return []

    paths: list[str] = []
    operations: list[str] = []
    actors: list[str] = []
    executables: list[str] = []
    timestamps: list[datetime] = []
    for record in records:
        timestamp = _audit_record_timestamp(record)
        if timestamp is not None:
            timestamps.append(timestamp)
        for path in _audit_field_values(record, 'name'):
            if path not in paths and len(paths) < _SSH_MAX_BREAKDOWN:
                paths.append(path)
        for operation in _audit_field_values(record, 'syscall'):
            if operation not in operations and len(operations) < _SSH_MAX_BREAKDOWN:
                operations.append(operation)
        for actor_field in ('acct', 'auid', 'uid'):
            for actor in _audit_field_values(record, actor_field):
                if actor not in actors and len(actors) < _SSH_MAX_BREAKDOWN:
                    actors.append(actor)
        for executable in _audit_field_values(record, 'exe'):
            if executable not in executables and len(executables) < _SSH_MAX_BREAKDOWN:
                executables.append(executable)

    event: JSONDict = {
        'type': 'auditd',
        'key': key,
        'severity': 'warning' if key in _CRITICAL_KEYS else 'info',
        'event_count': len(records),
        'meaning': _AUDIT_KEY_LABELS.get(key, 'security-sensitive system activity'),
    }
    if paths:
        event['paths'] = paths
    if operations:
        event['operations'] = operations
    if actors:
        event['actors'] = actors
    if executables:
        event['executables'] = executables
    if timestamps:
        event['first_seen'] = min(timestamps).isoformat(timespec='seconds')
        event['last_seen'] = max(timestamps).isoformat(timespec='seconds')
    return [event]


def _merge_audit_event(current: JSONDict | None, event: JSONDict) -> JSONDict:
    """Combine one bounded audit record into a bounded key summary."""
    if current is None:
        return event
    current['event_count'] = int(current['event_count']) + int(event['event_count'])
    for field in ('paths', 'operations', 'actors', 'executables'):
        incoming = event.get(field)
        if not isinstance(incoming, list) or not incoming:
            continue
        values = current.setdefault(field, [])
        if not isinstance(values, list):
            continue
        for value in incoming:
            if value not in values and len(values) < _SSH_MAX_BREAKDOWN:
                values.append(value)
    first_seen = event.get('first_seen')
    prior_first = current.get('first_seen')
    if isinstance(first_seen, str) and (
        not isinstance(prior_first, str) or first_seen < prior_first
    ):
        current['first_seen'] = first_seen
    last_seen = event.get('last_seen')
    prior_last = current.get('last_seen')
    if isinstance(last_seen, str) and (
        not isinstance(prior_last, str) or last_seen > prior_last
    ):
        current['last_seen'] = last_seen
    return current


def _stream_source_query(command: list[str], on_output) -> tuple[int | None, str | None]:
    """Process source output with a deadline and bounded callback records."""
    try:
        return run_streamed(
            command, timeout=_SOURCE_TIMEOUT_SECONDS, on_output=on_output
        ), None
    except TimeoutError:
        return None, 'query timed out'
    except (OSError, ValueError):
        return None, 'query failed or contained an oversized record'


def _ausearch_events(
    key: str,
    since: datetime,
    until: datetime,
    excluded_window: tuple[datetime, datetime] | None = None,
) -> tuple[list[JSONDict], str | None]:
    """Return summarised auditd events for a key and any collection error."""
    ausearch = _audit_tool('ausearch')
    if not ausearch:
        return [], f'audit key {key}: ausearch command unavailable'
    summary: JSONDict | None = None
    record_lines: list[str] = []
    record_bytes = 0

    def finish_record() -> None:
        nonlocal summary, record_bytes
        if record_lines:
            events = _parse_audit_events(key, ''.join(record_lines), excluded_window)
            if events:
                summary = _merge_audit_event(summary, events[0])
        record_lines.clear()
        record_bytes = 0

    def consume(line: str) -> None:
        nonlocal record_bytes
        if len(line.encode('utf-8')) > _MAX_SOURCE_LINE_BYTES:
            raise ValueError('audit line too large')
        if line.strip() == '----':
            finish_record()
            return
        record_bytes += len(line.encode('utf-8'))
        if record_bytes > _MAX_AUDIT_RECORD_BYTES:
            raise ValueError('audit record too large')
        record_lines.append(line)

    returncode, error = _stream_source_query(
        [
            ausearch, '--start', since.strftime('%m/%d/%Y'),
            since.strftime('%H:%M:%S'), '--end', until.strftime('%m/%d/%Y'),
            until.strftime('%H:%M:%S'), '-k', key, '-i',
        ],
        consume,
    )
    if error:
        return [], f'audit key {key}: {error}'
    if returncode == 0:
        finish_record()
        return [summary] if summary else [], None
    if returncode == 1:
        return [], None
    return [], f'audit key {key}: ausearch exited {returncode}'


def _check_auditd(
    since: datetime,
    until: datetime,
    excluded_window: tuple[datetime, datetime] | None = None,
) -> tuple[list[JSONDict], bool, list[str]]:
    """Return triggered keys, critical status, and collection errors."""
    if not _audit_tool('ausearch'):
        # auditd is deliberately optional on the dedicated Proxmox flow and
        # in containers.  If it is installed but ausearch disappeared, report
        # that the monitor cannot observe it instead of reporting a clean scan.
        if (
            os.environ.get('BASALTWATER_AUDIT_REQUIRED') == '1'
            or _audit_tool('auditd')
            or os.path.exists('/var/log/audit')
        ):
            return [], False, ['auditd: ausearch command unavailable']
        return [], False, []
    if os.environ.get('BASALTWATER_AUDIT_REQUIRED') == '1':
        health, issues = _audit_health()
        if health != 'ok':
            return [], False, [f'auditd: {issue}' for issue in issues]
    events: list[JSONDict] = []
    has_critical = False
    errors: list[str] = []
    for key in _CRITICAL_KEYS + _INFO_KEYS:
        key_events, error = _ausearch_events(key, since, until, excluded_window)
        if error:
            errors.append(error)
        if key_events:
            events.extend(key_events)
            if key in _CRITICAL_KEYS:
                has_critical = True
    return events, has_critical, errors


# ---------------------------------------------------------------------------
# SSH failures
# ---------------------------------------------------------------------------

def _parse_ssh_failure(message: str, timestamp: str | None) -> JSONDict | None:
    """Extract a useful, low-cardinality summary from one SSH log message."""
    failure_markers = (
        'Failed password',
        'Invalid user',
        'keyboard-interactive',
        'publickey',
        'authentication failure',
        'authentication attempt',
    )
    if not any(marker.lower() in message.lower() for marker in failure_markers):
        return None

    user = 'unknown'
    source_ip = 'unknown'
    method = 'unknown'
    failed_password = re.search(
        r'Failed password for (?:invalid user )?(?P<user>\S+) from (?P<source>\S+)',
        message,
        re.IGNORECASE,
    )
    invalid_user = re.search(
        r'Invalid user (?P<user>\S+) from (?P<source>\S+)',
        message,
        re.IGNORECASE,
    )
    generic_source = re.search(r'\bfrom (?P<source>\S+)', message, re.IGNORECASE)
    generic_user = re.search(r'\buser[= ](?P<user>\S+)', message, re.IGNORECASE)
    match = failed_password or invalid_user
    if match:
        user = match.group('user')
        source_ip = match.group('source')
    else:
        if generic_source:
            source_ip = generic_source.group('source').rstrip(';,')
        if generic_user:
            user = generic_user.group('user').rstrip(';,')
    user = user[:128]
    source_ip = source_ip[:128]

    lower_message = message.lower()
    if 'publickey' in lower_message:
        method = 'publickey'
    elif 'keyboard-interactive' in lower_message:
        method = 'keyboard-interactive'
    elif 'password' in lower_message:
        method = 'password'
    elif 'authentication failure' in lower_message:
        method = 'pam'

    event: JSONDict = {
        'source_ip': source_ip,
        'username': user,
        'method': method,
        'count': 1,
    }
    if timestamp:
        event['first_seen'] = timestamp
        event['last_seen'] = timestamp
    return event


def _parse_ssh_lockout(message: str, timestamp: str | None) -> JSONDict | None:
    """Extract PAM/faillock account-lockout events from SSH journal text."""
    lower_message = message.lower()
    if not any(
        marker in lower_message
        for marker in ('account temporarily locked', 'account locked', 'faillock')
    ):
        return None

    user = 'unknown'
    user_match = re.search(r'\buser[= ](?P<user>\S+)', message, re.IGNORECASE)
    if user_match:
        user = user_match.group('user').rstrip(';,')
    source_ip = 'unknown'
    source_match = re.search(r'\b(?:rhost|from)[= ](?P<source>\S+)', message, re.IGNORECASE)
    if source_match:
        source_ip = source_match.group('source').rstrip(';,')
    user = user[:128]
    source_ip = source_ip[:128]

    event: JSONDict = {
        'source_ip': source_ip,
        'username': user,
        'count': 1,
    }
    if timestamp:
        event['first_seen'] = timestamp
        event['last_seen'] = timestamp
    return event


def _normalise_ssh_summary(value: object) -> JSONDict:
    """Accept old integer test/provider results while using structured data."""
    if isinstance(value, dict):
        summary = dict(value)
        count = summary.get('failure_count', 0)
        summary['failure_count'] = count if isinstance(count, int) else 0
        sources = summary.get('sources', [])
        summary['sources'] = sources if isinstance(sources, list) else []
        lockouts = summary.get('lockouts', [])
        summary['lockouts'] = lockouts if isinstance(lockouts, list) else []
        return summary
    if isinstance(value, int):
        return {'failure_count': value, 'sources': [], 'lockouts': []}
    return {'failure_count': 0, 'sources': [], 'lockouts': []}


def _check_ssh_failures(since: datetime, until: datetime) -> tuple[JSONDict, str | None]:
    """Summarise SSH authentication failures and return collection errors."""
    aggregate: dict[tuple[str, str, str], JSONDict] = {}
    lockout_aggregate: dict[tuple[str, str], JSONDict] = {}
    failure_count = 0
    unattributed_failures = 0
    unattributed_lockouts = 0

    def consume(line: str) -> None:
        nonlocal failure_count, unattributed_failures, unattributed_lockouts
        if len(line.encode('utf-8')) > _MAX_SOURCE_LINE_BYTES:
            raise ValueError('SSH journal record too large')
        timestamp: str | None = None
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            return
        if not isinstance(record, dict):
            return
        message = record.get('MESSAGE')
        if not isinstance(message, str):
            return
        raw_timestamp = record.get('_SOURCE_REALTIME_TIMESTAMP')
        if isinstance(raw_timestamp, str) and raw_timestamp.isdigit():
            try:
                timestamp = datetime.fromtimestamp(
                    int(raw_timestamp) / 1_000_000
                ).isoformat(timespec='seconds')
            except (OSError, OverflowError, ValueError):
                timestamp = None

        lockout = _parse_ssh_lockout(message, timestamp)
        if lockout:
            lockout_key = (str(lockout['source_ip']), str(lockout['username']))
            current_lockout = lockout_aggregate.get(lockout_key)
            if current_lockout is None:
                if len(lockout_aggregate) < _SSH_MAX_GROUPS:
                    lockout_aggregate[lockout_key] = dict(lockout)
                else:
                    unattributed_lockouts += 1
            else:
                current_lockout['count'] = int(current_lockout.get('count', 0)) + 1
                if timestamp:
                    current_lockout['last_seen'] = timestamp

        event = _parse_ssh_failure(message, timestamp)
        if event:
            failure_count += 1
            key = (
                str(event['source_ip']),
                str(event['username']),
                str(event['method']),
            )
            current = aggregate.get(key)
            if current is None:
                if len(aggregate) < _SSH_MAX_GROUPS:
                    aggregate[key] = dict(event)
                else:
                    unattributed_failures += 1
            else:
                current['count'] = int(current.get('count', 0)) + 1
                if timestamp:
                    current['last_seen'] = timestamp

    returncode, error = _stream_source_query(
        ['journalctl', '-u', 'sshd', '-u', 'ssh',
         '--since', since.strftime('%Y-%m-%d %H:%M:%S'),
         '--until', until.strftime('%Y-%m-%d %H:%M:%S'),
         '--no-pager', '-o', 'json'],
        consume,
    )
    if error:
        return _normalise_ssh_summary(0), f'SSH journal: {error}'
    if returncode != 0:
        return _normalise_ssh_summary(0), f'SSH journal: journalctl exited {returncode}'

    sources = sorted(
        aggregate.values(),
        key=lambda entry: (-int(entry.get('count', 0)), str(entry.get('source_ip', ''))),
    )
    summary: JSONDict = {
        'failure_count': failure_count,
        'sources': sources[:_SSH_MAX_BREAKDOWN],
        'lockouts': sorted(
            lockout_aggregate.values(),
            key=lambda entry: (
                -int(entry.get('count', 0)),
                str(entry.get('source_ip', '')),
            ),
        )[:_SSH_MAX_BREAKDOWN],
    }
    if len(sources) > _SSH_MAX_BREAKDOWN:
        summary['suppressed_sources'] = len(sources) - _SSH_MAX_BREAKDOWN
    if unattributed_failures:
        summary['unattributed_failures'] = unattributed_failures
    if unattributed_lockouts:
        summary['unattributed_lockouts'] = unattributed_lockouts
    return summary, None


# ---------------------------------------------------------------------------
# XRDP certificate health
# ---------------------------------------------------------------------------

def _certificate_health_event(
    state: dict[str, object],
    health: XrdpCertificateHealth,
) -> tuple[str, str, str] | None:
    """Return a notification event only when certificate health changes."""
    previous_issue = state.get("rdp_certificate_issue")
    if not isinstance(previous_issue, str):
        previous_issue = ""
    previous_fingerprint = state.get("rdp_certificate_fingerprint")
    if not isinstance(previous_fingerprint, str):
        previous_fingerprint = ""

    current_issue = health.issue or ""
    if current_issue:
        fingerprint_unchanged = (
            not previous_fingerprint
            or not health.fingerprint
            or previous_fingerprint == health.fingerprint
        )
        if current_issue == previous_issue and fingerprint_unchanged:
            return None
        return (
            health.status,
            f"XRDP TLS certificate {health.status}",
            current_issue,
        )

    if health.status == "ok" and previous_issue:
        return (
            "info",
            "XRDP TLS certificate recovered",
            f"Certificate health is now valid: {health.certificate_path}",
        )

    if (
        health.status == "ok"
        and previous_fingerprint
        and health.fingerprint
        and previous_fingerprint != health.fingerprint
    ):
        return (
            "info",
            "XRDP TLS certificate changed",
            f"Certificate fingerprint changed: {health.certificate_path}",
        )
    return None


def _next_state(now: datetime, health: XrdpCertificateHealth) -> dict[str, object]:
    """Build persisted collection and certificate state."""
    next_state: dict[str, object] = {"last_run": now.isoformat()}
    if health.status != "not_configured":
        next_state["rdp_certificate_status"] = health.status
        next_state["rdp_certificate_issue"] = health.issue or ""
        next_state["rdp_certificate_fingerprint"] = health.fingerprint or ""
    return next_state


def _state_collection_errors(state: dict[str, object]) -> list[str]:
    """Return the previously observed collection errors from monitor state."""
    errors = state.get("collection_errors")
    if not isinstance(errors, list):
        return []
    return [error for error in errors if isinstance(error, str) and error]


def _normalise_collection_errors(errors: list[str | None]) -> list[str]:
    """Remove empty and duplicate collection errors while preserving order."""
    normalised: list[str] = []
    for error in errors:
        if error and error not in normalised:
            normalised.append(error)
    return normalised


def _normalise_audit_event(event: object) -> JSONDict:
    """Keep notification construction tolerant of legacy key-only results."""
    if isinstance(event, dict):
        return dict(event)
    key = str(event)
    return {
        'type': 'auditd',
        'key': key,
        'severity': 'warning' if key in _CRITICAL_KEYS else 'info',
        'event_count': 1,
        'meaning': _AUDIT_KEY_LABELS.get(key, 'security-sensitive system activity'),
    }


def _audit_event_keys(events: list[JSONDict]) -> list[str]:
    """Return unique audit keys in collection order."""
    keys: list[str] = []
    for event in events:
        key = event.get('key')
        if isinstance(key, str) and key not in keys:
            keys.append(key)
    return keys


def _collection_error_event(error: str) -> JSONDict:
    """Turn a human collection error into a routable source-health event."""
    source = 'unknown'
    if error.startswith(('audit key ', 'auditd:')):
        source = 'auditd'
    elif error.startswith('SSH journal:'):
        source = 'ssh_journal'
    elif error.startswith('fail2ban:') or error.startswith('fail2ban log:'):
        source = 'fail2ban'
    return {
        'type': 'source_health',
        'source': source,
        'state': 'unavailable',
        'severity': 'error',
        'details': error,
    }


def _format_fail2ban_event(event: JSONDict) -> str:
    """Format a fail2ban event for a human-readable notification."""
    if "summary" in event:
        return f"  {event['summary']}"
    return (
        f"  {event.get('timestamp', 'unknown time')} | "
        f"FAIL2BAN {str(event.get('action', 'event')).upper()} | "
        f"jail={event.get('jail', 'unknown')} | "
        f"source={event.get('source_ip', 'unknown')}"
    )


def _format_audit_event(event: JSONDict) -> str:
    """Format summarised audit evidence for an operator."""
    key = str(event.get('key', 'unknown'))
    parts = [
        str(event.get('meaning', _AUDIT_KEY_LABELS.get(key, 'security-sensitive activity'))),
        f"audit_key={key}",
    ]
    if 'event_count' in event:
        parts.append(f"events={event['event_count']}")
    if 'first_seen' in event:
        parts.append(f"first_seen={event['first_seen']}")
    if 'last_seen' in event and event.get('last_seen') != event.get('first_seen'):
        parts.append(f"last_seen={event['last_seen']}")
    for field in ('paths', 'operations', 'actors', 'executables'):
        values = event.get(field)
        if isinstance(values, list) and values:
            parts.append(f"{field}={','.join(str(value) for value in values)}")
    return "  - " + " | ".join(parts)


def _build_security_data(
    *,
    since: datetime,
    now: datetime,
    status: str,
    bans: list[JSONDict],
    unbans: list[JSONDict],
    ban_count: int,
    unban_count: int,
    audit_keys: list[str],
    ssh_failures: int,
    certificate_event: tuple[str, str, str] | None,
    ssh_summary: JSONDict | None = None,
    audit_events: list[JSONDict] | None = None,
    collection_errors: list[str] | None = None,
    collection_recovered: bool = False,
) -> JSONDict:
    """Build a stable webhook payload for security-monitor notifications."""
    critical_audit_keys = [key for key in audit_keys if key in _CRITICAL_KEYS]
    events: list[JSONDict] = []
    events.extend(dict(event) for event in bans)
    events.extend(dict(event) for event in unbans)
    if audit_events:
        events.extend(dict(event) for event in audit_events)
    else:
        events.extend(
            {
                "type": "auditd",
                "key": key,
                "severity": "warning" if key in _CRITICAL_KEYS else "info",
                "event_count": 1,
                "meaning": _AUDIT_KEY_LABELS.get(
                    key, "security-sensitive system activity"
                ),
            }
            for key in audit_keys
        )
    if ssh_failures >= _SSH_FAILURE_THRESHOLD:
        ssh_event: JSONDict = {
            "type": "ssh_authentication",
            "severity": "warning" if ssh_failures >= _SSH_WARNING_THRESHOLD else "info",
            "failure_count": ssh_failures,
            "reporting_threshold": _SSH_FAILURE_THRESHOLD,
        }
        if ssh_summary:
            ssh_event['sources'] = list(ssh_summary.get('sources', []))
            if 'suppressed_sources' in ssh_summary:
                ssh_event['suppressed_sources'] = ssh_summary['suppressed_sources']
            if 'unattributed_failures' in ssh_summary:
                ssh_event['unattributed_failures'] = ssh_summary['unattributed_failures']
        events.append(ssh_event)
    lockouts = ssh_summary.get('lockouts', []) if ssh_summary else []
    if isinstance(lockouts, list):
        events.extend(
            {
                'type': 'account_lockout',
                'severity': 'warning',
                **dict(lockout),
            }
            for lockout in lockouts
            if isinstance(lockout, dict)
        )
    if certificate_event:
        events.append(
            {
                "type": "xrdp_certificate",
                "severity": certificate_event[0],
                "change": certificate_event[1],
                "details": certificate_event[2],
            }
        )
    if collection_errors:
        events.extend(_collection_error_event(error) for error in collection_errors)
    if collection_recovered:
        events.append(
            {
                "type": "monitor_recovery",
                "severity": "info",
                "state": "resolved",
                "message": "Previously unavailable security event sources are readable again.",
            }
        )

    data: JSONDict = {
        "schema_version": _SECURITY_EVENT_SCHEMA_VERSION,
        "window": {"since": since.isoformat(), "until": now.isoformat()},
        "status": status,
        "counts": {
            "fail2ban_bans": ban_count,
            "fail2ban_unbans": unban_count,
            "fail2ban_bans_omitted": ban_count - len(bans),
            "fail2ban_unbans_omitted": unban_count - len(unbans),
            "auditd_critical": len(critical_audit_keys),
            "auditd_total": sum(
                int(event.get('event_count', 1))
                for event in (audit_events or [])
            ) if audit_events else len(audit_keys),
            "ssh_authentication_failures": ssh_failures,
            "account_lockouts": len(lockouts) if isinstance(lockouts, list) else 0,
            "events": len(events),
        },
        "events": events,
    }
    return data


def _format_security_details(
    *,
    since: datetime,
    now: datetime,
    status: str,
    bans: list[JSONDict],
    unbans: list[JSONDict],
    ban_count: int,
    unban_count: int,
    audit_keys: list[str],
    ssh_failures: int,
    certificate_event: tuple[str, str, str] | None,
    ssh_summary: JSONDict | None = None,
    audit_events: list[JSONDict] | None = None,
    collection_errors: list[str] | None = None,
    collection_recovered: bool = False,
) -> str:
    """Format security findings as a compact, operator-friendly report."""
    lines = [
        f"Window: {since.strftime('%Y-%m-%d %H:%M:%S')} → "
        f"{now.strftime('%Y-%m-%d %H:%M:%S')} (local time)",
        f"Overall status: {status.upper()}",
    ]
    if collection_errors:
        lines.extend([
            "",
            "Sources unavailable:",
            *[f"  - {error}" for error in collection_errors],
            "",
            "This is a monitoring-health problem, not evidence of an intrusion.",
            "Check security-monitor.service and the affected source.",
        ])
        return "\n".join(lines)

    lines.extend(["", "Summary:"])
    if ban_count:
        lines.append(f"  - Fail2ban bans: {ban_count}")
    if unban_count:
        lines.append(f"  - Fail2ban unbans: {unban_count}")
    if audit_keys:
        descriptions = [_AUDIT_KEY_LABELS.get(key, key) for key in audit_keys]
        lines.append(f"  - Protected changes: {', '.join(descriptions)}")
    if ssh_failures >= _SSH_FAILURE_THRESHOLD:
        lines.append(
            f"  - SSH authentication failures: {ssh_failures} "
            f"(threshold: {_SSH_FAILURE_THRESHOLD})"
        )
    lockouts = ssh_summary.get('lockouts', []) if ssh_summary else []
    if isinstance(lockouts, list) and lockouts:
        lines.append(f"  - Account lockouts: {len(lockouts)}")
    if certificate_event:
        lines.append(f"  - {certificate_event[1]}")
    if collection_recovered:
        lines.append("  - Security monitor recovered its event sources")

    if bans or unbans:
        lines.extend(["", "Fail2ban events:"])
        lines.extend(_format_fail2ban_event(event) for event in (*bans, *unbans))
        omitted = ban_count + unban_count - len(bans) - len(unbans)
        if omitted:
            lines.append(f"  - {omitted} more fail2ban event(s) omitted from details")
    if audit_keys:
        lines.extend([
            "",
            "Audit evidence:",
        ])
        if audit_events:
            lines.extend(_format_audit_event(event) for event in audit_events)
        else:
            lines.extend(
                f"  - {_AUDIT_KEY_LABELS.get(key, key)} | audit_key={key}"
                for key in audit_keys
            )
        lines.extend([
            "",
            "What this means:",
            "  - Auditd observed a security-sensitive system change.",
            "  - This is evidence to review, not confirmation that the host was compromised.",
        ])
    if ssh_failures >= _SSH_FAILURE_THRESHOLD:
        lines.extend([
            "",
            "SSH result:",
            f"  - {ssh_failures} failed authentication attempts in the window",
        ])
        if ssh_summary:
            for source in ssh_summary.get('sources', []):
                if not isinstance(source, dict):
                    continue
                lines.append(
                    "  - source={source} | user={user} | method={method} | count={count}".format(
                        source=source.get('source_ip', 'unknown'),
                        user=source.get('username', 'unknown'),
                        method=source.get('method', 'unknown'),
                        count=source.get('count', 0),
                    )
                )
            suppressed = ssh_summary.get('suppressed_sources', 0)
            if suppressed:
                lines.append(f"  - {suppressed} additional source group(s) omitted")
            unattributed = ssh_summary.get('unattributed_failures', 0)
            if unattributed:
                lines.append(
                    f"  - {unattributed} failure(s) counted without a source breakdown"
                )
    if isinstance(lockouts, list) and lockouts:
        lines.extend([
            "",
            "Account lockouts:",
            *[
                "  - source={source} | user={user} | count={count}".format(
                    source=lockout.get('source_ip', 'unknown'),
                    user=lockout.get('username', 'unknown'),
                    count=lockout.get('count', 0),
                )
                for lockout in lockouts
                if isinstance(lockout, dict)
            ],
        ])
    if certificate_event:
        lines.extend(["", "XRDP certificate:", f"  - {certificate_event[2]}"])

    actions = _security_actions(
        bans=bans,
        audit_keys=audit_keys,
        ssh_failures=ssh_failures,
        ssh_summary=ssh_summary,
        certificate_event=certificate_event,
        collection_recovered=collection_recovered,
    )
    lines.extend(["", "Suggested action:", *[f"  - {action}" for action in actions]])
    return "\n".join(lines)


def _security_actions(
    *,
    bans: list[JSONDict],
    audit_keys: list[str],
    ssh_failures: int,
    ssh_summary: JSONDict | None,
    certificate_event: tuple[str, str, str] | None,
    collection_recovered: bool,
) -> list[str]:
    """Return concise operator actions shared by text and webhook envelopes."""
    actions: list[str] = []
    if bans:
        actions.append("Review the banned source IPs and related authentication logs.")
    lockouts = ssh_summary.get('lockouts', []) if ssh_summary else []
    if isinstance(lockouts, list) and lockouts:
        actions.append("Confirm the locked accounts are expected and investigate repeated lockouts.")
    if ssh_failures >= _SSH_FAILURE_THRESHOLD and not bans:
        actions.append("Review SSH authentication logs for unexpected access.")
    if 'identity' in audit_keys:
        actions.append("Confirm recent account, group, or credential-file changes were approved.")
    if 'sudoers' in audit_keys:
        actions.append("Confirm recent sudo policy changes were approved and preserve least privilege.")
    if 'sshd_config' in audit_keys:
        actions.append("Confirm recent SSH server configuration changes were approved.")
    if 'modules' in audit_keys:
        actions.append("Confirm the reported kernel module activity was expected.")
    if certificate_event:
        actions.append("Check XRDP certificate/key health if the change was not planned.")
    if collection_recovered and not actions:
        actions.append("No action is required if the event sources are healthy now.")
    if not actions:
        actions.append("No immediate action is indicated by this report.")
    return actions


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    log_event(logger, "Starting security monitor check")

    notification_configs = load_notification_configs_from_state(logger)
    if not notification_configs:
        log_event(
            logger,
            "No notification targets configured; security events will be logged locally",
        )

    state = _load_state()
    wall_now = datetime.now()

    if 'last_run' in state:
        try:
            last_run = state['last_run']
            if not isinstance(last_run, str):
                raise TypeError('last_run must be a string')
            since = datetime.fromisoformat(last_run)
            if since.tzinfo is not None:
                since = since.astimezone().replace(tzinfo=None)
        except (TypeError, ValueError):
            since = wall_now - _SCAN_WINDOW
    else:
        since = wall_now - _SCAN_WINDOW
    if since > wall_now:
        since = wall_now - _SCAN_WINDOW
    since = since.replace(microsecond=0)
    now = min(
        wall_now.replace(microsecond=0) - timedelta(seconds=1),
        since + _SCAN_WINDOW - timedelta(seconds=1),
    )
    if now < since:
        log_event(logger, 'No complete second of security events to scan yet')
        return 0
    next_cursor = now + timedelta(seconds=1)

    fail2ban_scan = _check_fail2ban(since, now)
    bans, unbans = fail2ban_scan.bans, fail2ban_scan.unbans
    ban_count, unban_count = fail2ban_scan.ban_count, fail2ban_scan.unban_count
    audit_exclusion = managed_setup_audit_window(since, now)
    if audit_exclusion:
        log_event(
            logger,
            "Excluding auditd events from a managed basaltwater setup window",
            audit_window_start=audit_exclusion[0].isoformat(),
            audit_window_end=audit_exclusion[1].isoformat(),
        )
    audit_results, audit_critical, audit_errors = _check_auditd(
        since,
        now,
        excluded_window=audit_exclusion,
    )
    audit_events = [_normalise_audit_event(event) for event in audit_results]
    audit_keys = _audit_event_keys(audit_events)
    ssh_result, ssh_error = _check_ssh_failures(since, now)
    ssh_summary = _normalise_ssh_summary(ssh_result)
    ssh_failures = int(ssh_summary.get('failure_count', 0))
    certificate_health = inspect_xrdp_certificate()
    collection_errors = _normalise_collection_errors(
        [fail2ban_scan.error, *audit_errors, ssh_error]
    )
    previous_collection_errors = _state_collection_errors(state)
    if collection_errors:
        details = _format_security_details(
            since=since,
            now=now,
            status="error",
            bans=[],
            unbans=[],
            ban_count=0,
            unban_count=0,
            audit_keys=[],
            ssh_failures=0,
            certificate_event=None,
            collection_errors=collection_errors,
        )
        log_event(
            logger,
            'Security event collection failed; retaining previous cursor',
            level=WARNING,
            errors='; '.join(collection_errors),
        )
        if collection_errors != previous_collection_errors:
            send_notification_safe(
                notification_configs,
                subject='Security monitor error: event source unavailable',
                job='security_monitor',
                status='error',
                message=(
                    'The security monitor could not read one or more event '
                    'sources. This is a monitoring-health issue, not evidence '
                    'of an intrusion.'
                ),
                details=details,
                logger=logger,
                data=_build_security_data(
                    since=since,
                    now=now,
                    status='error',
                    bans=[],
                    unbans=[],
                    ban_count=0,
                    unban_count=0,
                    audit_keys=[],
                    ssh_failures=0,
                    certificate_event=None,
                    collection_errors=collection_errors,
                ),
                event_type='security.source_health',
                state='firing',
                dedup_key='security_monitor:source-health',
                actions=['Restore the affected event source and inspect security-monitor.service logs.'],
            )
        else:
            log_event(
                logger,
                'Security event collection still failing; notification suppressed',
                level=WARNING,
                errors='; '.join(collection_errors),
            )
        next_state = _next_state(since, certificate_health)
        next_state['collection_errors'] = collection_errors
        _save_state(next_state)
        return 1

    certificate_event = _certificate_health_event(state, certificate_health)
    certificate_failed = certificate_health.status == "error"
    critical_audit_keys = [key for key in audit_keys if key in _CRITICAL_KEYS]
    lockouts = ssh_summary.get('lockouts', [])
    has_lockouts = isinstance(lockouts, list) and bool(lockouts)
    collection_recovered = bool(previous_collection_errors)
    if unbans:
        log_event(
            logger,
            "Fail2ban ban expiry observed; external notification suppressed",
            unban_count=unban_count,
        )
    has_noteworthy = (
        bans
        or critical_audit_keys
        or ssh_failures >= _SSH_FAILURE_THRESHOLD
        or has_lockouts
        or certificate_event is not None
        or collection_recovered
    )
    if not has_noteworthy:
        if certificate_failed:
            log_event(
                logger,
                "XRDP TLS certificate remains unhealthy",
                level=ERROR,
                errors=certificate_health.issue or "unknown certificate error",
            )
            _save_state(_next_state(next_cursor, certificate_health))
            return 1
        log_event(logger, "No noteworthy security events")
        _save_state(_next_state(next_cursor, certificate_health))
        return 0

    # Determine notification severity.
    # Info-only audit keys (e.g. 'privileged') provide context but do not
    # create a notification by themselves because they cover routine sudo.
    certificate_status = certificate_event[0] if certificate_event else "info"
    if certificate_status == "error":
        status = 'error'
    elif (
        bans
        or critical_audit_keys
        or ssh_failures >= _SSH_WARNING_THRESHOLD
        or has_lockouts
        or certificate_status == "warning"
    ):
        status = 'warning'
    else:
        status = 'info'

    # Build subject summary
    summary_parts: list[str] = []
    if bans:
        summary_parts.append(
            f"{ban_count} fail2ban ban{'s' if ban_count != 1 else ''}"
        )
    if critical_audit_keys:
        summary_parts.extend(
            _AUDIT_KEY_LABELS.get(key, key) for key in critical_audit_keys
        )
    if ssh_failures >= _SSH_FAILURE_THRESHOLD:
        summary_parts.append(f"{ssh_failures} SSH failures")
    if has_lockouts:
        summary_parts.append(f"{len(lockouts)} account lockout{'s' if len(lockouts) != 1 else ''}")
    if certificate_event:
        summary_parts.append(certificate_event[1])
    if collection_recovered:
        summary_parts.append("monitor recovered")

    subject = f"Security {status}: {', '.join(summary_parts)}"
    since_str = since.strftime('%Y-%m-%d %H:%M:%S')
    details = _format_security_details(
        since=since,
        now=now,
        status=status,
        bans=bans,
        unbans=unbans,
        ban_count=ban_count,
        unban_count=unban_count,
        audit_keys=audit_keys,
        ssh_failures=ssh_failures,
        certificate_event=certificate_event,
        ssh_summary=ssh_summary,
        audit_events=audit_events,
        collection_recovered=collection_recovered,
    )
    data = _build_security_data(
        since=since,
        now=now,
        status=status,
        bans=bans,
        unbans=unbans,
        ban_count=ban_count,
        unban_count=unban_count,
        audit_keys=audit_keys,
        ssh_failures=ssh_failures,
        certificate_event=certificate_event,
        ssh_summary=ssh_summary,
        audit_events=audit_events,
        collection_recovered=collection_recovered,
    )

    actionable_events = bool(
        bans
        or critical_audit_keys
        or ssh_failures >= _SSH_FAILURE_THRESHOLD
        or has_lockouts
        or certificate_event
    )
    if collection_recovered and not actionable_events:
        event_type = 'security.source_health'
        state = 'resolved'
        dedup_key = 'security_monitor:source-health'
    elif certificate_event and not (bans or critical_audit_keys or ssh_failures >= _SSH_FAILURE_THRESHOLD):
        event_type = 'security.certificate'
        state = 'firing' if certificate_event[0] in ('error', 'warning') else 'resolved'
        dedup_key = 'security_monitor:xrdp-certificate'
    else:
        event_type = 'security.activity'
        state = 'firing'
        dedup_key = 'security_monitor:activity'

    actions = _security_actions(
        bans=bans,
        audit_keys=audit_keys,
        ssh_failures=ssh_failures,
        ssh_summary=ssh_summary,
        certificate_event=certificate_event,
        collection_recovered=collection_recovered,
    )

    send_notification_safe(
        notification_configs,
        subject=subject,
        job="security_monitor",
        status=status,
        message=(
            f"Security monitor found activity to review since {since_str}. "
            "The findings are evidence, not by themselves confirmation of a compromise. "
            "Review the summary and suggested actions below."
        ),
        details=details,
        logger=logger,
        data=data,
        event_type=event_type,
        state=state,
        dedup_key=dedup_key,
        actions=actions,
    )

    log_event(logger, "Security monitor check complete",
              status=status, events=', '.join(summary_parts),
              window=f"{since_str} → {now.strftime('%Y-%m-%d %H:%M:%S')}")
    _save_state(_next_state(next_cursor, certificate_health))
    return 1 if certificate_failed else 0


if __name__ == '__main__':
    sys.exit(main())
