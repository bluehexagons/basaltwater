"""Durable webhook receipts and recoverable publication of filesystem jobs."""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
import re
import shutil
import sqlite3
import stat
import time
from pathlib import Path

from lib.atomic_io import write_json_atomic
from lib.validation import validate_filesystem_path
from web.service_tools.cicd_security import MAX_JOB_FILE_BYTES, validate_job_data

MAX_PENDING_JOBS = 100
MAX_RECEIPTS = 10000
RECEIPT_TTL_SECONDS = 30 * 86400
JOB_TTL_SECONDS = 7 * 86400
MIN_FREE_BYTES = 128 * 1024 * 1024


@contextmanager
def _database(path: str, *, create: bool = False):
    validate_filesystem_path(path)
    if create:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    descriptor = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK |
                         (os.O_CREAT if create else 0), 0o600)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError('Delivery ledger must be a regular file')
    finally:
        os.close(descriptor)
    connection = sqlite3.connect(path, timeout=5, isolation_level=None)
    try:
        connection.execute('PRAGMA synchronous=FULL')
        # Bound retained ledger pages to 64 MiB with SQLite's default page size.
        page_size = connection.execute('PRAGMA page_size').fetchone()[0]
        connection.execute(f'PRAGMA max_page_count={64 * 1024 * 1024 // page_size}')
        connection.execute('BEGIN IMMEDIATE')
        version = connection.execute('PRAGMA user_version').fetchone()[0]
        if version == 0 and create:
            connection.execute('CREATE TABLE receipts (key TEXT PRIMARY KEY, payload TEXT NOT NULL, created REAL NOT NULL, state TEXT NOT NULL)')
            connection.execute('PRAGMA user_version=1')
        elif version != 1:
            raise ValueError('Unsupported delivery ledger schema')
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def _key(value: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r'[0-9a-f]{64}', value) is None:
        raise ValueError('Invalid delivery digest')
    return value


def _job_path(jobs_dir: str, key: str) -> str:
    validate_filesystem_path(jobs_dir)
    return os.path.join(jobs_dir, f'delivery-{_key(key)}.json')


def _publish(connection, jobs_dir: str, key: str) -> None:
    row = connection.execute('SELECT payload, state FROM receipts WHERE key=?', (key,)).fetchone()
    if row is None or row[1] != 'pending':
        return
    path = _job_path(jobs_dir, key)
    if not os.path.lexists(path):
        write_json_atomic(path, json.loads(row[0]))


def enqueue(ledger: str, jobs_dir: str, key: str, payload: dict) -> None:
    """Reserve durably before publishing; repeated signed payloads share a job."""
    _key(key)
    validate_job_data(payload)
    payload = {**payload, 'delivery_key': key}
    serialized = json.dumps(payload)
    if len(serialized.encode('utf-8')) > MAX_JOB_FILE_BYTES // 2:
        raise ValueError('Delivery job payload is too large')
    os.makedirs(jobs_dir, exist_ok=True)
    with _database(ledger, create=True) as connection:
        now = time.time()
        connection.execute("DELETE FROM receipts WHERE state != 'pending' AND created < ?", (now - RECEIPT_TTL_SECONDS,))
        existing = connection.execute('SELECT key FROM receipts WHERE key=?', (key,)).fetchone()
        if existing is None:
            count = connection.execute('SELECT count(*) FROM receipts').fetchone()[0]
            pending = {f'delivery-{row[0]}.json' for row in connection.execute(
                "SELECT key FROM receipts WHERE state='pending'"
            )}
            with os.scandir(jobs_dir) as entries:
                for entry in entries:
                    if entry.name.endswith('.json'):
                        pending.add(entry.name)
                    if len(pending) >= MAX_PENDING_JOBS:
                        break
            if count >= MAX_RECEIPTS or len(pending) >= MAX_PENDING_JOBS:
                raise ValueError('CI/CD queue or delivery ledger is full; retry later')
            if shutil.disk_usage(jobs_dir).free < MIN_FREE_BYTES:
                raise ValueError('Insufficient free space for CI/CD admission')
            connection.execute('INSERT INTO receipts VALUES (?, ?, ?, ?)', (key, serialized, now, 'pending'))
    # The committed reservation survives interruption before file publication.
    # Publication and executor claims serialize on the same database lock.
    with _database(ledger) as connection:
        _publish(connection, jobs_dir, key)


def recover_pending(ledger: str, jobs_dir: str) -> None:
    """Re-publish reserved jobs after a receiver restart without replaying claims."""
    if not os.path.lexists(ledger):
        return
    with _database(ledger) as connection:
        keys = connection.execute("SELECT key FROM receipts WHERE state='pending'").fetchall()
        if len(keys) > MAX_PENDING_JOBS:
            raise ValueError('Delivery ledger exceeds the pending job limit')
        for (key,) in keys:
            _publish(connection, jobs_dir, key)


def pending_job_files(ledger: str, jobs_dir: str) -> list[Path]:
    """Order recovered and live jobs by acceptance, never by their digest."""
    created = {}
    if os.path.lexists(ledger):
        with _database(ledger) as connection:
            created = {f'delivery-{key}.json': timestamp for key, timestamp in
                       connection.execute('SELECT key, created FROM receipts')}
    jobs = []
    for path in Path(jobs_dir).glob('*.json'):
        try:
            timestamp = created[path.name] if path.name in created else path.lstat().st_mtime
        except FileNotFoundError:
            continue
        jobs.append((timestamp, path.name, path))
    return [path for _, _, path in sorted(jobs)]


def claim(ledger: str, job_file: str, payload: dict) -> bool:
    """Persist an at-most-once attempt before executing any build side effect.

    A crash after claiming requires operator review, never automatic replay.
    Pending jobs older than seven days expire without running deployment code.
    """
    key = _key(payload.get('delivery_key'))
    if os.path.basename(job_file) != f'delivery-{key}.json':
        raise ValueError('Delivery job filename does not match its receipt')
    with _database(ledger) as connection:
        row = connection.execute('SELECT payload, created, state FROM receipts WHERE key=?', (key,)).fetchone()
        if row is None or json.loads(row[0]) != payload:
            raise ValueError('Delivery receipt is missing or does not match the job')
        if row[2] != 'pending':
            return False
        state = 'expired' if row[1] < time.time() - JOB_TTL_SECONDS else 'claimed'
        connection.execute('UPDATE receipts SET state=? WHERE key=?', (state, key))
        return state == 'claimed'
