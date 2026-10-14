"""Private, bounded project notes, todos and references to existing secret files."""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import time
from typing import Any, Iterator
import uuid

from lib.agent_inbox import mailbox_name
from lib.atomic_io import parse_json, write_json_atomic
from lib.remote_utils import CommandTimeoutError, run as run_command
from lib.validation import validate_filesystem_path, validate_no_control_characters


MAX_ENTRIES = 256
MAX_TEXT_BYTES = 16 * 1024
MAX_STORAGE_BYTES = 4 * 1024 * 1024
_KINDS = ("note", "todo", "secret")
_STATUSES = ("open", "claimed", "done", "blocked")
_TOKEN = re.compile(r"[0-9a-f]{32}\Z")


class MemoryConflictError(RuntimeError):
    """The observed revision or cooperative task ownership is no longer current."""


class MemoryBusyError(RuntimeError):
    """Another process still owns this project's short write transaction."""


def _project_scope(project: str) -> str:
    validate_filesystem_path(project, must_exist=True)
    root = str(Path(project).resolve())
    validate_filesystem_path(root, must_exist=True)
    if not os.path.isdir(root):
        raise ValueError("Project memory requires a project directory")
    git = shutil.which("git")
    if git:
        environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        environment["LC_ALL"] = "C"
        try:
            result = run_command(
                [git, "-C", root, "rev-parse", "--path-format=absolute", "--git-common-dir"],
                check=False, capture_output=True, text=True, timeout=10, env=environment,
            )
        except CommandTimeoutError as exc:
            raise RuntimeError("Git project-scope discovery timed out") from exc
        if result.returncode == 0:
            common = result.stdout.strip()
            validate_filesystem_path(common, must_exist=True)
            if not os.path.isabs(common) or not os.path.isdir(common):
                raise RuntimeError("Git returned an invalid common directory")
            return "git:" + str(Path(common).resolve())
        if "not a git repository" not in (result.stderr or "").lower():
            raise RuntimeError("Cannot determine Git project scope; inspect repository access")
    return "directory:" + root


def _kind(value: str) -> str:
    if value not in _KINDS:
        raise ValueError("Memory kind must be note, todo or secret")
    return value


def _text(value: str) -> str:
    if (not isinstance(value, str) or not value.strip() or len(value.encode("utf-8")) > MAX_TEXT_BYTES
            or any(ord(char) < 32 and char not in "\n\r\t" or ord(char) == 127 for char in value)):
        raise ValueError("Memory text requires printable UTF-8, at most 16384 bytes")
    return value


def _private_file(info: os.stat_result) -> None:
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or info.st_mode & 0o077 or info.st_nlink != 1):
        raise RuntimeError("Memory files must be private, singly linked regular files owned by this account")


class AgentMemory:
    """Same-account cooperation, with atomic writes and optimistic revisions.

    Claim labels/tokens coordinate cooperating agents; they do not authenticate
    agents sharing a Unix account. Records never execute commands or grant scope.
    """

    def __init__(self, project: str = ".", *, home: str | None = None) -> None:
        self.home = Path(os.path.abspath(home or os.path.expanduser("~")))
        self.identity = _project_scope(os.path.abspath(os.path.expanduser(project)))
        self.scope = hashlib.sha256(self.identity.encode("utf-8")).hexdigest()
        self.root = self.home / ".local/state/basaltwater/project-memory"
        self.directory = self.root / self.scope
        self.path = self.directory / "records.json"

    def _directory(self, *, create: bool) -> bool:
        validate_filesystem_path(str(self.home), must_exist=True)
        current = self.home
        for component in (None, *self.directory.relative_to(self.home).parts):
            if component is not None:
                current /= component
                if create and not os.path.lexists(current):
                    current.mkdir(mode=0o700, exist_ok=True)
            if not os.path.lexists(current):
                return False
            info = current.lstat()
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
                    or info.st_mode & 0o022 or (current in (self.root, self.directory) and info.st_mode & 0o077)):
                raise RuntimeError("Unsafe project-memory directory ownership, permissions or link")
        return True

    @contextmanager
    def _transaction(self) -> Iterator[dict[str, Any]]:
        self._directory(create=True)
        descriptor = os.open(self.directory / "memory.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        try:
            _private_file(os.fstat(descriptor))
            deadline = time.monotonic() + 5
            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise MemoryBusyError("Project memory is busy; retry after the current writer finishes")
                    time.sleep(0.05)
            state = self._load()
            yield state
            if len(state["entries"]) > MAX_ENTRIES or len((json.dumps(state, indent=2) + "\n").encode("utf-8")) > MAX_STORAGE_BYTES:
                raise ValueError("Project memory is full; remove obsolete records explicitly")
            write_json_atomic(str(self.path), state)
        finally:
            os.close(descriptor)

    def _load(self) -> dict[str, Any]:
        empty = {"version": 2, "scope": self.identity, "entries": {}, "next_revision": 1}
        if not self._directory(create=False):
            return empty
        try:
            descriptor = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return empty
        try:
            _private_file(os.fstat(descriptor))
            with os.fdopen(descriptor, "rb") as stream:
                descriptor = -1
                content = stream.read(MAX_STORAGE_BYTES + 1)
            if len(content) > MAX_STORAGE_BYTES:
                raise ValueError("Memory state exceeds its storage bound")
            state = parse_json(content)
            if (not isinstance(state, dict) or type(state.get("version")) is not int
                    or state["version"] not in (1, 2)):
                raise ValueError("Unsupported memory schema")
            expected = {"version", "scope", "entries"}
            if state["version"] == 2:
                expected.add("next_revision")
            if (set(state) != expected
                    or state["scope"] != self.identity or not isinstance(state["entries"], dict)
                    or len(state["entries"]) > MAX_ENTRIES):
                raise ValueError("Invalid memory schema or scope")
            for key, record in state["entries"].items():
                self._validate_record(key, record)
            highest_revision = max((record["revision"] for record in state["entries"].values()), default=0)
            if state["version"] == 1:
                # Preserve old observations; persist migration only with a successful write.
                state.update(version=2, next_revision=highest_revision + 1)
            elif type(state["next_revision"]) is not int or state["next_revision"] <= highest_revision:
                raise ValueError("Invalid project revision counter")
            return state
        except (ValueError, TypeError, KeyError) as exc:
            raise RuntimeError("Invalid project memory; preserve its state for inspection") from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    @staticmethod
    def _validate_record(key: str, record: dict[str, Any]) -> None:
        if not isinstance(record, dict):
            raise ValueError("Invalid memory record")
        kind, name = _kind(record["kind"]), mailbox_name(record["name"])
        expected = {"kind", "name", "revision", "updated_at"}
        expected |= {"file"} if kind == "secret" else {"text"}
        if kind == "todo":
            expected |= {"status", "claim"}
        if (set(record) != expected or key != kind + ":" + name
                or type(record["revision"]) is not int or record["revision"] < 1
                or type(record["updated_at"]) is not int or record["updated_at"] < 0):
            raise ValueError("Invalid memory record metadata")
        if kind == "secret":
            validate_filesystem_path(record["file"])
            if not os.path.isabs(record["file"]):
                raise ValueError("Secret references require an absolute path")
        else:
            _text(record["text"])
        if kind == "todo":
            if record["status"] not in _STATUSES:
                raise ValueError("Invalid todo status")
            claim = record["claim"]
            if record["status"] != "claimed":
                if claim is not None:
                    raise ValueError("Inactive todo cannot retain a claim")
            elif (not isinstance(claim, dict) or set(claim) != {"owner", "token", "expires_at"}
                    or not isinstance(claim["token"], str) or not _TOKEN.fullmatch(claim["token"])
                    or type(claim["expires_at"]) is not int or claim["expires_at"] < 0):
                raise ValueError("Invalid todo claim")
            else:
                mailbox_name(claim["owner"])

    def _secret_file(self, value: str) -> str:
        validate_filesystem_path(value)
        path = Path(os.path.abspath(os.path.expanduser(value)))
        validate_filesystem_path(str(path), must_exist=True)
        if not path.is_relative_to(self.home):
            raise ValueError("Secret references must stay below this account's home")
        current = self.home
        for component in path.parent.relative_to(self.home).parts:
            current /= component
            info = current.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022:
                raise RuntimeError("Unsafe secret-reference directory")
        _private_file(path.lstat())
        return str(path)

    @staticmethod
    def _metadata(record: dict[str, Any]) -> dict[str, Any]:
        result = {key: value for key, value in record.items() if key not in ("text", "file", "claim")}
        if record["kind"] == "todo":
            claim = record["claim"]
            result["claim"] = {key: claim[key] for key in ("owner", "expires_at")} if claim else None
        return result

    @staticmethod
    def _record(entries: dict[str, Any], kind: str, name: str) -> dict[str, Any]:
        key = _kind(kind) + ":" + mailbox_name(name)
        if key not in entries:
            raise FileNotFoundError("Project-memory record was not found")
        return entries[key]

    @staticmethod
    def _revision(record: dict[str, Any] | None, revision: int) -> None:
        if type(revision) is not int or revision < 0:
            raise ValueError("Revision must be a nonnegative integer")
        if revision != (record["revision"] if record else 0):
            raise MemoryConflictError("Memory revision changed; inspect the record before retrying")

    @staticmethod
    def _next_revision(state: dict[str, Any]) -> int:
        """Allocate under the project lock; deletion must never reuse a revision."""
        revision = state["next_revision"]
        state["next_revision"] += 1
        return revision

    @staticmethod
    def _live_claim(record: dict[str, Any]) -> bool:
        return record.get("claim") is not None and record["claim"]["expires_at"] > int(time.time())

    def put(self, kind: str, name: str, value: str, *, revision: int = 0) -> dict[str, Any]:
        kind, name = _kind(kind), mailbox_name(name)
        value = self._secret_file(value) if kind == "secret" else _text(value)
        with self._transaction() as state:
            entries = state["entries"]
            key = kind + ":" + name
            previous = entries.get(key)
            self._revision(previous, revision)
            if previous and kind == "todo" and self._live_claim(previous):
                raise MemoryConflictError("Todo has a live claim; finish or release it before editing")
            record = {"kind": kind, "name": name, "revision": self._next_revision(state), "updated_at": int(time.time()),
                      "file" if kind == "secret" else "text": value}
            if kind == "todo":
                record.update(status=previous["status"] if previous else "open", claim=previous["claim"] if previous else None)
            entries[key] = record
        return self._metadata(record)

    def get(self, kind: str, name: str) -> dict[str, Any]:
        record = self._record(self._load()["entries"], kind, name)
        result = self._metadata(record)
        if kind == "secret":
            result["file"] = self._secret_file(record["file"])
        else:
            result["text"] = record["text"]
        return result

    def list(self, *, kind: str | None = None, limit: int = 20, query: str = "") -> dict[str, Any]:
        if kind is not None:
            _kind(kind)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("List limit must be 1–100")
        if not isinstance(query, str):
            raise ValueError("Memory query must be text")
        validate_no_control_characters(query, "Memory query")
        if len(query) > 128:
            raise ValueError("Memory query must be at most 128 characters")
        records = [record for record in self._load()["entries"].values()
                   if (record["kind"] == kind if kind else record["kind"] != "secret")
                   and query.casefold() in (record["name"] + " " + record.get("text", "")).casefold()]
        records.sort(key=lambda record: (-record["updated_at"], record["kind"], record["name"]))
        return {"scope": self.scope, "total": len(records), "truncated": len(records) > limit,
                "entries": [self._metadata(record) for record in records[:limit]]}

    def remove(self, kind: str, name: str, *, revision: int) -> dict[str, Any]:
        with self._transaction() as state:
            entries = state["entries"]
            record = self._record(entries, kind, name)
            self._revision(record, revision)
            if kind == "todo" and self._live_claim(record):
                raise MemoryConflictError("Todo has a live claim; finish or release it before removal")
            del entries[kind + ":" + name]
        return {"kind": kind, "name": name, "removed": True}

    @staticmethod
    def _lease(seconds: int) -> None:
        if type(seconds) is not int or not 30 <= seconds <= 86400:
            raise ValueError("Todo leases must be 30–86400 seconds")

    def claim(self, name: str, owner: str, *, seconds: int = 900) -> dict[str, Any]:
        mailbox_name(owner)
        self._lease(seconds)
        with self._transaction() as state:
            record = self._record(state["entries"], "todo", name)
            if record["status"] not in ("open", "claimed") or self._live_claim(record):
                raise MemoryConflictError("Todo is unavailable; inspect its status and claim")
            record.update(status="claimed", claim={"owner": owner, "token": uuid.uuid4().hex,
                                                   "expires_at": int(time.time()) + seconds})
            record.update(revision=self._next_revision(state), updated_at=int(time.time()))
        return {**self._metadata(record), "claim_token": record["claim"]["token"]}

    def complete_claim(self, name: str, token: str, *, status: str | None = None, seconds: int = 900) -> dict[str, Any]:
        if not isinstance(token, str) or not _TOKEN.fullmatch(token):
            raise ValueError("Invalid todo claim token")
        if status is not None and status not in ("open", "done", "blocked"):
            raise ValueError("Finish status must be open, done or blocked")
        self._lease(seconds)
        with self._transaction() as state:
            record = self._record(state["entries"], "todo", name)
            if not self._live_claim(record) or record["claim"]["token"] != token:
                raise MemoryConflictError("Todo claim expired or changed; inspect it before retrying")
            if status is None:
                record["claim"]["expires_at"] = int(time.time()) + seconds
            else:
                record.update(status=status, claim=None)
            record.update(revision=self._next_revision(state), updated_at=int(time.time()))
        return self._metadata(record)

    def reopen(self, name: str, *, revision: int) -> dict[str, Any]:
        with self._transaction() as state:
            record = self._record(state["entries"], "todo", name)
            self._revision(record, revision)
            if record["status"] not in ("done", "blocked"):
                raise MemoryConflictError("Only done or blocked todos can be reopened")
            record.update(status="open", claim=None, revision=self._next_revision(state), updated_at=int(time.time()))
        return self._metadata(record)
