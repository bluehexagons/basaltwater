"""Inspect live coding work without treating persistent agent servers as busy."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import stat

from lib.validation import validate_filesystem_path


_PROVIDERS = frozenset({"codex", "claude", "opencode"})
_ROLLOUT_LIMIT = 1024 * 1024


def _one_shot(process: Path, name: str) -> bool:
    """Recognize executing CLI modes, excluding app servers and open prompts."""
    with (process / "cmdline").open("rb") as handle:
        arguments = handle.read(4096).split(b"\0")
    if name == "claude":
        return b"-p" in arguments[1:] or b"--print" in arguments[1:]
    return len(arguments) > 1 and arguments[1] in (
        (b"exec", b"e", b"review") if name == "codex" else (b"run",)
    )


def _codex_turn_active(descriptor: Path) -> bool:
    """Read lifecycle events only from a rollout held open by a live provider.

    No paths or event contents are returned. An open writer with a long turn
    whose start lies outside the bounded tail remains active until a terminal
    lifecycle event is seen. Closed historical rollouts are never scanned.
    """
    descriptor_fd = os.open(descriptor, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
    with os.fdopen(descriptor_fd, "rb") as handle:
        metadata = os.fstat(handle.fileno())
        if not stat.S_ISREG(metadata.st_mode) or not metadata.st_size:
            return False
        offset = max(0, metadata.st_size - _ROLLOUT_LIMIT)
        handle.seek(offset)
        if offset:
            handle.readline(_ROLLOUT_LIMIT)
        lines = handle.read(_ROLLOUT_LIMIT).splitlines()
    for line in reversed(lines):
        try:
            event = json.loads(line)
        except (ValueError, UnicodeError):
            continue
        if not isinstance(event, dict) or event.get("type") != "event_msg":
            continue
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue
        kind = payload.get("type")
        if kind in {"task_started", "turn_started"}:
            return True
        if kind in {"task_complete", "turn_complete", "turn_aborted", "shutdown_complete"}:
            return False
    return bool(offset)


def _process_started_at(process: Path, proc_root: Path) -> str:
    """Bind persisted activity to the current server process, including reboot."""
    boot_time = None
    with (proc_root / "stat").open(encoding="ascii") as handle:
        for line in handle:
            if line.startswith("btime "):
                boot_time = int(line.split()[1])
                break
    if boot_time is None:
        raise ValueError("Process boot time is unavailable")
    # comm may contain spaces and parentheses; fields after its last ')' start
    # at field 3, so starttime (field 22) has index 19 in this suffix.
    fields = (process / "stat").read_text(encoding="utf-8").rsplit(")", 1)[1].split()
    started = boot_time + int(fields[19]) / os.sysconf("SC_CLK_TCK")
    return datetime.fromtimestamp(started, timezone.utc).isoformat()


def _t3_work_active(database: Path, started_at: str, uid: int) -> bool:
    """Query only status/time columns, using the live WAL and no database writes."""
    metadata = database.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != uid:
        raise ValueError("Unsafe T3 activity database")
    validate_filesystem_path(str(database), must_exist=True)
    connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=1)
    try:
        connection.execute("PRAGMA query_only = ON")
        connection.execute("PRAGMA trusted_schema = OFF")
        # Bound reads even if an unexpectedly large or damaged projection is
        # supplied. SQLite busy waits are separately bounded by timeout above.
        remaining = 10000

        def progress() -> int:
            nonlocal remaining
            remaining -= 1
            return int(remaining <= 0)

        connection.set_progress_handler(progress, 1000)
        if database.name == "statev2.sqlite":
            query = """
                SELECT 1 FROM orchestration_v2_projection_runs AS run
                WHERE status IN ('preparing', 'starting', 'running')
                  AND julianday(requested_at) >= julianday(?)
                  AND NOT EXISTS (
                    SELECT 1 FROM orchestration_v2_projection_runtime_requests AS request
                    WHERE request.thread_id = run.thread_id AND request.status = 'pending'
                  )
                UNION ALL
                SELECT 1 FROM orchestration_v2_projection_provider_turns AS turn
                WHERE status = 'running' AND julianday(started_at) >= julianday(?)
                  AND NOT EXISTS (
                    SELECT 1 FROM orchestration_v2_projection_runtime_requests AS request
                    WHERE request.thread_id = turn.thread_id AND request.status = 'pending'
                  )
                LIMIT 1
            """
            parameters = (started_at, started_at)
        else:
            query = """
                SELECT 1 FROM projection_thread_sessions
                WHERE status = 'running' AND active_turn_id IS NOT NULL
                  AND julianday(updated_at) >= julianday(?) LIMIT 1
            """
            parameters = (started_at,)
        return connection.execute(query, parameters).fetchone() is not None
    finally:
        connection.close()


def active_agent_workloads(uid: int, *, proc_root: str = "/proc") -> list[str] | None:
    """Return category-only live work, or None when its state cannot be read.

    Long-lived servers, terminals, multiplexer sessions, and working directories
    are not activity signals. Discover T3 state through descriptors of processes
    that actually have its database open, rather than scanning saved history.
    """
    validate_filesystem_path(proc_root, must_exist=True)
    root = Path(proc_root)
    categories: set[str] = set()
    databases: dict[Path, str] = {}
    database_owners: set[int] = set()
    parents: dict[int, int] = {}
    providers: list[tuple[Path, str, list[Path]]] = []
    try:
        processes = list(root.iterdir())
        for process in processes:
            if not process.name.isdigit():
                continue
            try:
                metadata = process.lstat()
                if metadata.st_uid != uid or not stat.S_ISDIR(metadata.st_mode):
                    continue
                name = (process / "comm").read_text(encoding="utf-8").strip()
                fields = (process / "stat").read_text(encoding="utf-8").rsplit(")", 1)[1].split()
                parents[int(process.name)] = int(fields[1])
                rollouts: list[Path] = []
                for descriptor in (process / "fd").iterdir():
                    try:
                        target = descriptor.readlink()
                    except FileNotFoundError:
                        continue
                    if target.parent.name == "userdata" and target.name in {
                        "state.sqlite", "statev2.sqlite",
                    } and target.is_absolute():
                        started_at = _process_started_at(process, root)
                        databases[target] = min(databases.get(target, started_at), started_at)
                        database_owners.add(int(process.name))
                    elif name == "codex" and target.suffix == ".jsonl" and (
                        "sessions" in target.parts and target.name.startswith("rollout-")
                    ):
                        rollouts.append(descriptor)
                if name in _PROVIDERS:
                    providers.append((process, name, rollouts))
            except (FileNotFoundError, ProcessLookupError):
                continue  # Normal /proc race, not a saved activity lease.
        for database, started_at in databases.items():
            if _t3_work_active(database, started_at, uid):
                categories.add("T3 agent work")
        for process, name, rollouts in providers:
            ancestor = int(process.name)
            seen = set()
            while ancestor in parents and ancestor not in seen and ancestor not in database_owners:
                seen.add(ancestor)
                ancestor = parents[ancestor]
            if ancestor in database_owners:
                continue  # T3 knows whether this provider is running or waiting.
            try:
                if _one_shot(process, name):
                    categories.add("coding agent task")
                elif any(_codex_turn_active(descriptor) for descriptor in rollouts):
                    categories.add("coding agent turn")
            except (FileNotFoundError, ProcessLookupError):
                continue
    except (OSError, ValueError, IndexError, OverflowError, sqlite3.Error):
        return None
    return sorted(categories)
