"""Sanitized, immutable local catalog for paginating all exported findings."""

from __future__ import annotations

from contextlib import closing
import os
import sqlite3
import stat
import time

from common.web_panel_events import WEB_PANEL_AUDIT_DIR

CATALOG_PATH = os.path.join(WEB_PANEL_AUDIT_DIR, "storage-findings.sqlite3")
MAX_CATALOG_BYTES = 128 * 1024 * 1024
PAGE_SIZE = 20
MAX_PAGE = 1_000_000


class FindingCatalog:
    """Write a fresh private file; the supervisor publishes it after success."""

    def __init__(self, path: str, generated_at: str) -> None:
        os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600))
        self.connection = sqlite3.connect(path)
        self.connection.execute("PRAGMA journal_mode=OFF")
        self.connection.execute("PRAGMA synchronous=OFF")
        self.connection.executescript("""
            CREATE TABLE metadata (version INTEGER, generated_at TEXT);
            CREATE TABLE jobs (id INTEGER PRIMARY KEY, directory TEXT, search_directory TEXT,
                database_path TEXT, interval TEXT, status TEXT, last_scan TEXT, open_count INTEGER);
            CREATE TABLE findings (id INTEGER PRIMARY KEY, job INTEGER, path TEXT, search_path TEXT,
                category TEXT, first_seen TEXT, last_checked TEXT, revision TEXT);
            CREATE INDEX finding_category ON findings(category);
        """)
        self.connection.execute("INSERT INTO metadata VALUES (2, ?)", (generated_at,))

    def add_job(self, job: dict) -> int:
        return self.connection.execute("INSERT INTO jobs(directory,search_directory,database_path,interval,status,last_scan,open_count) VALUES (?,?,?,?,?,?,?)",
            (job["directory"], job["directory"].casefold(), job["database"], job["interval"], "unavailable", "", 0)).lastrowid

    def add_finding(self, job_id: int, finding: dict) -> None:
        self.connection.execute("INSERT INTO findings(job,path,search_path,category,first_seen,last_checked,revision) VALUES (?,?,?,?,?,?,?)",
            (job_id, finding["path"], finding["path"].casefold(), finding["category"],
             finding["first_seen"], finding["last_checked"], finding["revision"]))

    def finish_job(self, job_id: int, job: dict) -> None:
        self.connection.execute("UPDATE jobs SET status=?,last_scan=?,open_count=? WHERE id=?",
            (job["status"], job["last_scan"], job["open_count"], job_id))

    def close(self) -> None:
        try:
            self.connection.commit()
        finally:
            self.connection.close()


def _load_health(connection: sqlite3.Connection, query: dict[str, str]) -> dict:
    """Keep task inspection bounded and independent of finding filters."""
    from common.service_tools.web_panel_storage import STATUSES, _text, _timestamp

    try:
        search = query.get("task", "").casefold()
        total = connection.execute("SELECT count(*) FROM jobs WHERE instr(search_directory,?)>0", (search,)).fetchone()[0]
        page = min(int(query.get("tasks_page", "1")), max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE))
        rows = connection.execute("SELECT directory,database_path,interval,status,last_scan,open_count FROM jobs "
            "WHERE instr(search_directory,?)>0 ORDER BY id LIMIT ? OFFSET ?",
            (search, PAGE_SIZE, (page - 1) * PAGE_SIZE)).fetchall()
        jobs = []
        for directory, database, interval, status, last_scan, count in rows:
            if (status not in STATUSES or type(count) is not int or count < 0
                    or status == "verified" and (not last_scan or count)
                    or status == "findings" and not count
                    or status == "incomplete" and (not last_scan or count)
                    or status == "never_scanned" and (last_scan or count)):
                raise ValueError("Invalid task health")
            jobs.append({"directory": _text(directory), "database": _text(database), "interval": _text(interval),
                         "status": status, "last_scan": _timestamp(last_scan) if last_scan != "" else "",
                         "open_count": count})
        return {"available": True, "jobs": jobs, "total": total, "page": page}
    except (ValueError, TypeError, sqlite3.Error, OverflowError):
        return {"available": False}


def load_findings(snapshot: dict, query: dict[str, str], path: str = CATALOG_PATH) -> dict:
    """Read one bounded page from a pinned local inode; never read a NAS report."""
    from common.service_tools.web_panel_storage import _text, _timestamp
    from lib.data_actions import HEX_DIGEST
    from sync.service_tools.scrub_findings import ADVICE

    reference = snapshot.get("catalog")
    if reference is None:
        return {"available": False, "legacy": True}
    descriptor = None
    try:
        if (not isinstance(reference, dict) or type(reference.get("version")) is not int
                or reference["version"] not in {1, 2}
                or reference != {"version": reference["version"], "generated_at": snapshot["generated_at"]}):
            raise ValueError("Invalid catalog reference")
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_CATALOG_BYTES:
            raise ValueError("Invalid findings catalog size")
        deadline = time.monotonic() + 0.25
        with closing(sqlite3.connect(f"file:/proc/self/fd/{descriptor}?mode=ro&immutable=1", uri=True, timeout=0.1)) as connection:
            connection.set_progress_handler(lambda: time.monotonic() > deadline, 1000)
            if connection.execute("SELECT version,generated_at FROM metadata LIMIT 2").fetchall() != [(reference["version"], snapshot["generated_at"])]:
                raise ValueError("Catalog generation changed")
            category, search = query.get("category", ""), query.get("q", "").casefold()
            conditions, parameters = [], []
            if category:
                conditions.append("f.category=?")
                parameters.append(category)
            if search:
                conditions.append("instr(f.search_path,?)>0")
                parameters.append(search)
            where = "WHERE " + " AND ".join(conditions) if conditions else ""
            total = connection.execute("SELECT count(*) FROM findings f " + where, parameters).fetchone()[0]
            page = min(int(query.get("page", "1")), max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE))
            rows = connection.execute("SELECT j.directory,j.database_path,f.path,f.category,f.first_seen,f.last_checked,f.revision "
                "FROM findings f JOIN jobs j ON j.id=f.job " + where + " ORDER BY f.id LIMIT ? OFFSET ?",
                (*parameters, PAGE_SIZE, (page - 1) * PAGE_SIZE)).fetchall()
            unavailable = connection.execute("SELECT count(*) FROM jobs WHERE status='unavailable'").fetchone()[0]
            tasks = connection.execute("SELECT count(*) FROM jobs").fetchone()[0]
            health = _load_health(connection, query) if reference["version"] == 2 else {"available": False, "legacy": True}
        findings = []
        for directory, database, file, category, first, checked, revision in rows:
            if category not in ADVICE or not isinstance(revision, str) or not HEX_DIGEST.fullmatch(revision):
                raise ValueError("Invalid catalog finding")
            findings.append(({"directory": _text(directory), "database": _text(database)},
                {"path": _text(file), "category": category, "first_seen": _timestamp(first),
                 "last_checked": _timestamp(checked), "revision": revision}))
        return {"available": True, "findings": findings, "total": total, "page": page,
                "unavailable_jobs": unavailable, "tasks": tasks, "health": health}
    except (OSError, ValueError, TypeError, sqlite3.Error, OverflowError):
        return {"available": False, "legacy": False}
    finally:
        if descriptor is not None:
            os.close(descriptor)
