"""Private manual desktop evidence, separate from project-owned assertions."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import secrets
import re
import stat
import subprocess

from desktop.observations import compact_capture
from desktop.qa_artifacts import clock, stamp
from lib.atomic_io import read_json_file, write_json_atomic
from lib.streamed_process import run_captured
from lib.validation import validate_filesystem_path, validate_no_control_characters

EVENT_LIMIT = 500
CAPTURE_BYTES_LIMIT = 64 * 1024 * 1024 + 1024


def capture_digest(fd: int) -> str:
    """Hash bounded, stable PNG file bytes without moving the descriptor offset."""
    before = os.fstat(fd)
    if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid()
            or before.st_mode & 0o077 or not 0 < before.st_size <= CAPTURE_BYTES_LIMIT):
        raise ValueError("Capture must be a private owned regular file within the PNG size limit")
    digest = hashlib.sha256()
    offset = 0
    while offset < before.st_size:
        block = os.pread(fd, min(1024 * 1024, before.st_size - offset), offset)
        if not block:
            raise ValueError("Capture changed while hashing")
        digest.update(block)
        offset += len(block)
    after = os.fstat(fd)
    if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
        raise ValueError("Capture changed while hashing")
    return digest.hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _directory() -> Path:
    folder = Path.home()
    for component in (".local", "state", "basaltwater", "desktop-evidence"):
        folder /= component
        validate_filesystem_path(str(folder))
        folder.mkdir(mode=0o700, exist_ok=True)
        info = folder.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & (0o077 if component == "desktop-evidence" else 0o022)):
            raise RuntimeError("Unsafe desktop evidence directory")
    return folder


def revision(project: str) -> dict:
    """Observe Git without project hooks, filesystem monitors or index writes."""
    result = {"commit": None, "dirty": None}
    environment = {"PATH": "/usr/bin:/bin", "LC_ALL": "C", "GIT_OPTIONAL_LOCKS": "0",
                   "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"}
    prefix = ["/usr/bin/git", "-c", "core.fsmonitor=false", "-C", project]
    try:
        head = run_captured([*prefix, "rev-parse", "--verify", "HEAD"], timeout=2, max_bytes=1024, env=environment)
        status = run_captured([*prefix, "status", "--porcelain", "--untracked-files=normal"],
                              timeout=2, max_bytes=65536, env=environment)
        if head.returncode == 0 and re.fullmatch(r"[0-9a-f]{40,64}", head.stdout.strip()):
            result["commit"] = head.stdout.strip()
        if status.returncode == 0:
            result["dirty"] = bool(status.stdout)
    except (OSError, RuntimeError, subprocess.SubprocessError):
        pass
    return result


class Recorder:
    def __init__(self, project: str, generation: str, *, launches: dict | None = None):
        validate_filesystem_path(project, must_exist=True)
        path = Path(project)
        if not path.is_absolute() or not path.is_dir():
            raise ValueError("Evidence project must be an absolute existing directory")
        self.path = _directory() / f"{secrets.token_hex(16)}.json"
        self.launch_baseline = set(launches or {})
        self.data = {"schema_version": 1, "project": str(path.resolve()), "generation": generation,
                     "started_at": _now(), "finished_at": None, "finalized": False,
                     "clock": clock(), "started_monotonic_ns": stamp()["monotonic_ns"],
                     "revision_start": revision(str(path)), "revision_end": None,
                     "events": [], "events_truncated": False, "renderer": None,
                     "tasks": [], "task_inventory_truncated": False}
        self.save()

    def save(self):
        write_json_atomic(str(self.path), self.data, mode=0o600)

    def event(self, payload: dict, result: dict | None):
        action = payload.get("action")
        if action not in ("input", "element", "exec", "screenshot", "launch-status", "control"):
            return
        if len(self.data["events"]) >= EVENT_LIMIT:
            self.data["events_truncated"] = True
        else:
            row = {"action": action, **stamp(), "outcome": "returned" if result is not None else "uncertain"}
            # Never record text, key values, argv, element content or portal tokens.
            for key in ("kind", "x", "y", "to_x", "to_y", "duration_ms", "button", "hold_ms", "operation", "observation_id"):
                if key in payload:
                    row[key] = payload[key]
            if result is not None:
                row["result"] = compact_capture(result) if action == "screenshot" else {
                    key: result[key] for key in ("pid", "launch", "returncode", "state", "generation",
                        "input_completed_at", "input_completed_monotonic_ns", "observation_id") if key in result}
                if "error" in result:
                    row["outcome"] = "uncertain"
            self.data["events"].append(row)
        self.save()

    def finish(self, launches: dict, *, reason: str = "finished") -> dict:
        self.data["finished_at"] = _now()
        self.data["finished_monotonic_ns"] = stamp()["monotonic_ns"]
        self.data["recording_end_reason"] = reason
        self.data["native_launches"] = [{"launch": token, "pid": process.pid, "returncode": process.poll()}
                                        for token, process in launches.items() if token not in self.launch_baseline]
        self.save()
        return {"artifact": str(self.path), "events": len(self.data["events"]),
                "events_truncated": self.data["events_truncated"], "finalized": False}


def _load_record(path: str) -> dict:
    from desktop.development import TASK_ID
    validate_filesystem_path(path, must_exist=True)
    artifact = Path(path)
    if artifact.parent != _directory() or not TASK_ID.fullmatch(artifact.stem) or artifact.suffix != ".json":
        raise ValueError("Expected a desktop evidence artifact")
    info = artifact.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError("Evidence must be an owned private regular file")
    data = read_json_file(path, max_bytes=8 * 1024 * 1024)
    if (not isinstance(data, dict) or type(data.get("schema_version")) is not int or data["schema_version"] != 1
            or not all(key in data for key in ("project", "started_at", "finished_at", "events", "events_truncated", "tasks", "renderer"))
            or not isinstance(data["events"], list) or len(data["events"]) > EVENT_LIMIT
            or any(not isinstance(row, dict) for row in data["events"])
            or type(data["events_truncated"]) is not bool
            or not isinstance(data["tasks"], list) or len(data["tasks"]) > 50
            or any(not isinstance(row, dict) or not isinstance(row.get("task"), str)
                   or not TASK_ID.fullmatch(row["task"]) for row in data["tasks"])):
        raise ValueError("Invalid or unsupported desktop evidence record")
    return data


def verify(path: str) -> dict:
    """Check recorded capture file bytes without input, finalization or repairs."""
    data = _load_record(path)
    counts = {"verified": 0, "missing": 0, "mismatched": 0, "unverified": 0}
    problems = []
    for index, row in enumerate(data["events"]):
        if row.get("action") != "screenshot":
            continue
        result = row.get("result")
        result = result if isinstance(result, dict) else {}
        output, expected = result.get("output"), result.get("artifact_sha256")
        state, detail = "unverified", "Capture has no valid PNG file hash or path (older or uncertain recording)"
        if (isinstance(output, str) and isinstance(expected, str)
                and re.fullmatch(r"[0-9a-f]{64}", expected)):
            try:
                validate_filesystem_path(output)
                if not Path(output).is_absolute() or Path(output).suffix.lower() != ".png":
                    raise ValueError("Expected an absolute PNG path")
                fd = os.open(output, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                try:
                    actual = capture_digest(fd)
                finally:
                    os.close(fd)
                state = "verified" if actual == expected else "mismatched"
                detail = "PNG file bytes differ from the recorded hash"
            except FileNotFoundError:
                state, detail = "missing", "Recorded capture is unavailable"
            except (OSError, ValueError) as exc:
                detail = str(exc)[:256]
        counts[state] += 1
        if state != "verified" and len(problems) < 10:
            problems.append({"event_index": index, "output": output if isinstance(output, str) else None,
                             "state": state, "detail": detail})
    checked = sum(counts.values())
    failures = checked - counts["verified"]
    return {"artifact": path, "checked": checked, "counts": counts,
            "healthy": checked > 0 and failures == 0 and not data["events_truncated"],
            "events_truncated": data["events_truncated"], "finalized": data.get("finalized") is True,
            "problems": problems, "problems_truncated": failures > len(problems),
            "scope": "Recorded PNG file bytes only; no game outcome, pixel decoding, task logs or omitted events verified"}


def finalize(path: str, *, tasks: list[str], renderer: str | None) -> dict:
    """Collect supervised task status outside the portal's short request budget."""
    from desktop.development import TASK_ID, list_tasks, parse_task_timestamp, task_status
    data = _load_record(path)
    for timestamp in (data["started_at"], data["finished_at"]):
        if not isinstance(timestamp, str) or len(timestamp) > 40 or datetime.fromisoformat(timestamp).tzinfo is None:
            raise ValueError("Finish recording before finalizing evidence; timestamps must include timezones")
    if parse_task_timestamp(data["started_at"]) > parse_task_timestamp(data["finished_at"]):
        raise ValueError("Recording finish time must not precede its start")
    if len(tasks) > 50 or any(not isinstance(task, str) or not TASK_ID.fullmatch(task) for task in tasks):
        raise ValueError("Attach at most 50 valid task identifiers")
    if renderer is not None:
        validate_no_control_characters(renderer, "Observed renderer")
        if not renderer or len(renderer) > 512:
            raise ValueError("Observed renderer must contain 1–512 characters")
    listing = list_tasks(data["project"], since=data["started_at"], until=data["finished_at"])
    automatic = [row["task"] for row in listing["tasks"]]
    previous = [row["task"] for row in data["tasks"]]
    selected = list(dict.fromkeys([*tasks, *previous, *automatic]))
    data["task_inventory_truncated"] = listing["truncated"] or len(selected) > 50
    data["task_inventory_scan_truncated"] = listing.get("scan_truncated", False)
    snapshots = []
    for task in selected[:50]:
        try:
            snapshot = task_status(task)
            if snapshot["project"] != data["project"]:
                raise ValueError("Attached task belongs to another project")
            snapshots.append(snapshot)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            snapshots.append({"task": task, "state": "unverified", "error": str(exc)[:512]})
    data.update(tasks=snapshots, renderer=({"value": renderer, "source": "operator-observed"} if renderer else data["renderer"]),
                revision_end=revision(data["project"]), finalized=True, finalized_at=_now())
    write_json_atomic(path, data, mode=0o600)
    return {"artifact": path, "events": len(data["events"]), "events_truncated": data["events_truncated"],
            "tasks": [{"task": row["task"], "state": row["state"]} for row in snapshots],
            "task_inventory_truncated": data["task_inventory_truncated"], "finalized": True}
