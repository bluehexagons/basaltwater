"""Read a stable, bounded set of Fail2ban's numeric log rotations."""

from __future__ import annotations

import gzip
import os
import re
import shutil
import stat
import time
import zlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterator

from lib.types import JSONDict
from lib.validation import validate_filesystem_path, validate_network_ip

_MAX_FILES = 64
_MAX_BYTES = 256 * 1024 * 1024
_MAX_LINE_BYTES = 64 * 1024
_MAX_EXAMPLES = 50
_TIMEOUT = 30
_PREFIX = re.compile(
    r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+\s+'
    r'(?P<logger>fail2ban(?:\.[A-Za-z0-9_.-]+)?)\s+\[\d+\]:\s+(?P<level>[A-Z]+)\s+'
)
_ACTION = re.compile(r'\[([^\]]+)\] (Ban|Unban) (\S+)')


@dataclass
class Fail2banScan:
    """Exact totals, bounded examples and the known retained source floor."""

    bans: list[JSONDict] = field(default_factory=list)
    unbans: list[JSONDict] = field(default_factory=list)
    ban_count: int = 0
    unban_count: int = 0
    error: str | None = None
    oldest: datetime | None = None


class _Changed(Exception):
    """The file set changed; a new complete snapshot is required."""


@dataclass(frozen=True)
class _Log:
    path: str
    generation: int
    compressed: bool
    signature: tuple[int, ...]


def _signature(info: os.stat_result) -> tuple[int, ...]:
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


@dataclass
class _Budget:
    deadline: float = field(default_factory=lambda: time.monotonic() + _TIMEOUT)
    used: int = 0

    def check(self, size: int = 0) -> None:
        self.used += size
        if self.used > _MAX_BYTES:
            raise ValueError('decompressed data exceeded the size limit')
        if time.monotonic() >= self.deadline:
            raise ValueError('scan deadline exceeded')


def _inventory(path: str, budget: _Budget) -> tuple[_Log, ...]:
    directory, basename = os.path.split(path)
    rotations: dict[int, _Log] = {}
    with os.scandir(directory) as entries:
        for entry in entries:
            budget.check()
            if entry.name == basename:
                generation, compressed = 0, False
            elif entry.name.startswith((basename + '.', basename + '-')):
                match = re.fullmatch(re.escape(basename) + r'\.([1-9]\d{0,8})(\.gz)?', entry.name)
                if not match:
                    raise ValueError('unsupported rotation name or compression')
                generation, compressed = int(match[1]), bool(match[2])
            else:
                continue
            if generation in rotations:
                raise ValueError('ambiguous plain/compressed rotation')
            info = entry.stat(follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode):
                raise ValueError('log is not a regular file')
            if len(rotations) >= _MAX_FILES:
                raise ValueError('too many retained log files')
            rotations[generation] = _Log(entry.path, generation, compressed, _signature(info))
    generations = sorted(generation for generation in rotations if generation)
    if any(generation != expected for expected, generation in enumerate(generations, 1)):
        raise ValueError('retained rotation sequence has a missing generation')
    return tuple(rotations[generation] for generation in sorted(rotations, reverse=True))


def _lines(log: _Log, budget: _Budget) -> Iterator[str]:
    budget.check()
    descriptor = os.open(log.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as raw:
        info = os.fstat(raw.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise ValueError('log is not a regular file')
        if _signature(info) != log.signature:
            raise _Changed()
        if log.compressed and info.st_size == 0:
            raise ValueError('empty compressed archive')
        stream = gzip.GzipFile(fileobj=raw) if log.compressed else raw
        try:
            while line := stream.readline(_MAX_LINE_BYTES + 1):
                budget.check(len(line))
                if len(line) > _MAX_LINE_BYTES:
                    raise ValueError('line exceeded the size limit')
                if not line.endswith(b'\n'):
                    raise ValueError('incomplete final log record')
                yield line.decode('utf-8')
            # Reading to EOF also validates every gzip member's CRC/trailer.
            if _signature(os.fstat(raw.fileno())) != log.signature:
                raise _Changed()
        finally:
            if log.compressed:
                stream.close()


def _scan_once(path: str, since: datetime, until: datetime, budget: _Budget) -> Fail2banScan:
    logs = _inventory(path, budget)
    if not any(log.generation == 0 for log in logs):
        if logs or shutil.which('fail2ban-client'):
            raise ValueError('active log file unavailable')
        return Fail2banScan()
    scan = Fail2banScan()
    identities: dict[tuple[int, int], bool] = {}
    for log in logs:
        identity = log.signature[:2]
        if identity in identities:
            if identities[identity] != log.compressed:
                raise ValueError('aliased log has ambiguous compression')
            continue  # Hard-link aliases are the same records, not new events.
        identities[identity] = log.compressed
        for line in _lines(log, budget):
            prefix = _PREFIX.match(line)
            if not prefix:
                continue  # Traceback/continuation lines carry no source time.
            timestamp = datetime.strptime(prefix[1], '%Y-%m-%d %H:%M:%S')
            scan.oldest = min(timestamp, scan.oldest) if scan.oldest else timestamp
            if not since <= timestamp <= until:
                continue
            if prefix['logger'] != 'fail2ban.actions' or prefix['level'] not in ('NOTICE', 'WARNING'):
                continue
            action = _ACTION.match(line, prefix.end())
            if action is None:
                continue
            jail, operation, ip = action.groups()
            if len(jail) > 128 or not re.fullmatch(r'[A-Za-z0-9_.-]+', jail):
                jail = 'unknown'
            try:
                ip = validate_network_ip(ip, 'fail2ban source')
            except ValueError:
                ip = 'unknown'
            entry: JSONDict = {
                'type': 'fail2ban', 'action': operation.lower(),
                'severity': 'warning' if operation == 'Ban' else 'info',
                'timestamp': prefix[1], 'jail': jail, 'source_ip': ip,
            }
            if operation == 'Ban':
                scan.ban_count += 1
                if len(scan.bans) < _MAX_EXAMPLES:
                    scan.bans.append(entry)
            else:
                scan.unban_count += 1
                if len(scan.unbans) < _MAX_EXAMPLES:
                    scan.unbans.append(entry)
    if _inventory(path, budget) != logs:
        raise _Changed()
    if scan.oldest is None:
        raise ValueError('retention evidence unavailable; no timestamped records')
    return scan


def scan_fail2ban(path: str, since: datetime, until: datetime) -> Fail2banScan:
    """Read a stable numeric rename/create rotation set, retrying a race once.

    A changed attempt contributes no counts. Identical event text in distinct
    files remains distinct; only hard-link aliases are skipped. Date-suffixed,
    copytruncate and non-gzip policies are outside the packaged rotation scope.
    """
    try:
        validate_filesystem_path(path)
        if not os.path.isabs(path) or os.path.normpath(path) != path:
            raise ValueError('log path must be absolute and normalized')
        budget = _Budget()
        for attempt in range(2):
            try:
                return _scan_once(path, since, until, budget)
            except (FileNotFoundError, _Changed):
                if attempt:
                    raise ValueError('logs changed during collection; retry required')
    except (OSError, UnicodeError, ValueError, EOFError, zlib.error) as exc:
        return Fail2banScan(error=f'fail2ban log: {exc}')
    raise AssertionError('Unreachable scan attempt')
