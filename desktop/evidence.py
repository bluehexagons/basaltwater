"""Private manual desktop evidence, separate from project-owned assertions."""

from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import secrets
import re
import stat
import subprocess

from desktop.observations import compact_capture
from lib.atomic_io import read_json_file, write_json_atomic
from lib.streamed_process import run_captured
from lib.validation import validate_filesystem_path, validate_no_control_characters

EVENT_LIMIT = 500


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
            row = {"action": action, "at": _now(), "outcome": "returned" if result is not None else "uncertain"}
            # Never record text, key values, argv, element content or portal tokens.
            for key in ("kind", "x", "y", "to_x", "to_y", "duration_ms", "button", "hold_ms", "operation"):
                if key in payload:
                    row[key] = payload[key]
            if result is not None:
                row["result"] = compact_capture(result) if action == "screenshot" else {
                    key: result[key] for key in ("pid", "launch", "returncode", "state", "generation",
                        "input_completed_at", "input_completed_monotonic_ns") if key in result}
                if "error" in result:
                    row["outcome"] = "uncertain"
            self.data["events"].append(row)
        self.save()

    def finish(self, launches: dict, *, reason: str = "finished") -> dict:
        self.data["finished_at"] = _now()
        self.data["recording_end_reason"] = reason
        self.data["native_launches"] = [{"launch": token, "pid": process.pid, "returncode": process.poll()}
                                        for token, process in launches.items() if token not in self.launch_baseline]
        self.save()
        return {"artifact": str(self.path), "events": len(self.data["events"]),
                "events_truncated": self.data["events_truncated"], "finalized": False}


def finalize(path: str, *, tasks: list[str], renderer: str | None) -> dict:
    """Collect supervised task status outside the portal's short request budget."""
    from desktop.development import TASK_ID, list_tasks, task_status
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
            or not isinstance(data["tasks"], list) or len(data["tasks"]) > 50
            or any(not isinstance(row, dict) or not isinstance(row.get("task"), str)
                   or not TASK_ID.fullmatch(row["task"]) for row in data["tasks"])):
        raise ValueError("Invalid or unsupported desktop evidence record")
    for timestamp in (data["started_at"], data["finished_at"]):
        if not isinstance(timestamp, str) or len(timestamp) > 40 or datetime.fromisoformat(timestamp).tzinfo is None:
            raise ValueError("Finish recording before finalizing evidence; timestamps must include timezones")
    if len(tasks) > 50 or any(not isinstance(task, str) or not TASK_ID.fullmatch(task) for task in tasks):
        raise ValueError("Attach at most 50 valid task identifiers")
    if renderer is not None:
        validate_no_control_characters(renderer, "Observed renderer")
        if not renderer or len(renderer) > 512:
            raise ValueError("Observed renderer must contain 1–512 characters")
    listing = list_tasks(data["project"])
    automatic = [row["task"] for row in listing["tasks"]
                 if data["started_at"] <= row["created_at"] <= data["finished_at"]]
    previous = [row["task"] for row in data["tasks"]]
    selected = list(dict.fromkeys([*tasks, *previous, *automatic]))
    data["task_inventory_truncated"] = listing["truncated"] or len(selected) > 50
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
