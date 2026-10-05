"""Private durable publishing records, approvals and dispatch identities."""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import time
import uuid

from lib.validation import validate_filesystem_path, validate_no_control_characters


def identifier(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}", value):
        raise ValueError("Use an identifier of 1–80 letters, numbers, underscores or hyphens")
    return value


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def private_directory(path: Path) -> Path:
    """Reject linked ancestors and insecure owned state, never silently chmod it."""
    validate_filesystem_path(str(path), must_exist=False)
    path = path.absolute()
    for ancestor in (*reversed(path.parents), path):
        if ancestor.is_symlink():
            raise ValueError("Publishing paths cannot have symbolic links")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError("Publishing state must be owned by this account with mode 0700")
    return path


def private_file(path: Path) -> None:
    if not path.exists() and not path.is_symlink():
        return
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError("Publishing files must be private owned regular files without links")


@contextmanager
def file_lock(path: Path, *, blocking: bool = False):
    private_file(path)
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        yield fd
    except BlockingIOError as exc:
        raise RuntimeError("Publishing account is busy") from exc
    finally:
        os.close(fd)


class PublishingStore:
    """Short serialized transactions, including human approval at dispatch."""

    def __init__(self, home: str | None = None):
        self.home = Path(home or Path.home()).absolute()
        validate_filesystem_path(str(self.home), must_exist=False)
        self.root = self.home / ".local/share/basaltwater/publishing"

    @contextmanager
    def transaction(self):
        private_directory(self.root)
        with file_lock(self.root / "state.lock", blocking=True):
            path = self.root / "state.sqlite3"
            private_file(path)
            # Pre-create securely: sqlite's default file mode follows umask.
            fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            os.close(fd)
            try:
                connection = sqlite3.connect(path, timeout=10)
                try:
                    connection.execute("PRAGMA synchronous=FULL")
                    connection.execute("CREATE TABLE IF NOT EXISTS records (kind TEXT, id TEXT, document TEXT, PRIMARY KEY(kind,id))")
                    connection.execute("CREATE TABLE IF NOT EXISTS dispatches (identity TEXT PRIMARY KEY, run TEXT NOT NULL)")
                    connection.execute("CREATE TABLE IF NOT EXISTS reviews (revision TEXT PRIMARY KEY, hash TEXT, principal TEXT, reviewed REAL)")
                    connection.commit()
                    connection.execute("BEGIN IMMEDIATE")
                    yield connection
                    connection.commit()
                except BaseException:
                    connection.rollback()
                    raise
                finally:
                    connection.close()
            except sqlite3.Error:
                # Disk exhaustion, corruption and SQL errors use the same
                # bounded recovery surface as other private-state failures.
                raise RuntimeError("Publishing database is unavailable; inspect private state and backups") from None

    @staticmethod
    def get(connection, kind: str, record_id: str) -> dict:
        row = connection.execute("SELECT document FROM records WHERE kind=? AND id=?", (kind, identifier(record_id))).fetchone()
        if not row:
            raise ValueError("Publishing record not found")
        return json.loads(row[0])

    @staticmethod
    def put(connection, kind: str, value: dict) -> dict:
        identifier(value["id"])
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
        limit = 32 * 1024 * 1024 if kind == "artifacts" else 256 * 1024
        if len(encoded.encode()) > limit:
            raise ValueError("Publishing record exceeds size limit")
        connection.execute("INSERT OR REPLACE INTO records VALUES(?,?,?)", (kind, value["id"], encoded))
        return value

    @staticmethod
    def records(connection, kind: str) -> list[dict]:
        return [json.loads(row[0]) for row in connection.execute("SELECT document FROM records WHERE kind=? ORDER BY rowid DESC", (kind,))]

    def snapshot(self) -> dict:
        with self.transaction() as db:
            result = {}
            actionable = {
                "jobs": ("enabled", "paused"),
                "runs": ("queued", "running", "unknown", "uploaded-unverified"),
                "drafts": ("needs-review", "changes-requested", "approved", "awaiting-editor"),
                "releases": ("prepared-beta", "promotion-queued", "awaiting-steamworks", "unknown"),
            }
            for kind in ("projects", "artifacts", "runs", "jobs", "drafts", "releases", "accounts"):
                records = []
                states = actionable.get(kind, ())
                priority = "CASE WHEN json_extract(document,'$.state') IN (" + ",".join("?" for _ in states) + ") THEN 0 ELSE 1 END," if states else ""
                for row in db.execute("SELECT document FROM records WHERE kind=? ORDER BY " + priority + " rowid DESC LIMIT 200", (kind, *states)):
                    value = json.loads(row[0])
                    if kind == "artifacts":
                        value["file_count"] = len(value.pop("entries"))
                    records.append(value)
                result[kind] = records
            # A bounded history must still include the exact source text and
            # project needed to interpret each selected revision or operation.
            sources = {draft["source"] for draft in result["drafts"] if draft["source"]} - {draft["id"] for draft in result["drafts"]}
            result["drafts"].extend(self.get(db, "drafts", source) for source in sorted(sources))
            projects = {record["project"] for kind in ("artifacts", "runs", "jobs", "drafts", "releases") for record in result[kind]}
            projects -= {project["id"] for project in result["projects"]}
            result["projects"].extend(self.get(db, "projects", project) for project in sorted(projects))
            reviews = {}
            for draft in result["drafts"]:
                row = db.execute("SELECT hash,principal,reviewed FROM reviews WHERE revision=?", (draft["id"],)).fetchone()
                if row:
                    reviews[draft["id"]] = {"hash": row[0], "principal": row[1], "at": row[2]}
        for draft in result["drafts"]:
            draft["review"] = reviews.get(draft["id"])
        return result

    @staticmethod
    def new_id() -> str:
        return uuid.uuid4().hex


def safe_text(value: object, *, limit: int = 4096) -> str:
    if not isinstance(value, str) or not value or len(value) > limit:
        raise ValueError("Text is missing or too long")
    validate_no_control_characters(value, "publishing text")
    return value


def now() -> float:
    return time.time()
