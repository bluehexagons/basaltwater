"""Private task checkpoints, managed workspaces and bounded Codex event receipts."""

from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path
from typing import Any

from lib.atomic_io import read_json_file, write_json_atomic
from lib.validation import validate_filesystem_path


PERSISTENCE_MODES = ("fresh", "checkpoint", "conversation")
WORKSPACE_MODES = ("existing", "directory", "worktree", "run-directory")
OUTCOMES = ("progress", "complete", "waiting", "blocked", "failed")
MAX_CHECKPOINT_BYTES = 16000
MAX_CONTEXT_BYTES = 64 * 1024
MAX_EVENT_BYTES = 1024 * 1024
_ID = re.compile(r"[a-f0-9]{32}\Z")
_SESSION = re.compile(r"[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}\Z")

FOLLOW_UP_SCHEMA = {
    "type": ["object", "null"], "additionalProperties": False,
    "properties": {
        "title": {"type": "string"}, "reason": {"type": "string"}, "prompt": {"type": "string", "maxLength": 3800},
        "directory": {"type": "string"}, "mode": {"type": "string", "enum": ["inspect", "workspace"]},
        "workspace": {"type": "string", "enum": list(WORKSPACE_MODES)},
        "network": {"type": "boolean"}, "temporary_files": {"type": "boolean"},
    },
    "required": ["title", "reason", "prompt", "directory", "mode", "workspace", "network", "temporary_files"],
}

REPORT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "outcome": {"type": "string", "enum": list(OUTCOMES)},
        "summary": {"type": "string"},
        "checkpoint": {"type": "string"},
        "evidence": {"type": "array", "items": {"type": "string"}},
        "retry_after_minutes": {"type": ["integer", "null"], "minimum": 1, "maximum": 43200},
        "follow_up": FOLLOW_UP_SCHEMA,
    },
    "required": ["outcome", "summary", "checkpoint", "evidence", "retry_after_minutes", "follow_up"],
}


def validate_follow_up(value: object) -> dict[str, Any] | None:
    """Validate a bounded proposal, which never grants authority or queues work."""
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != set(FOLLOW_UP_SCHEMA["required"]):
        raise ValueError("Invalid task follow-up request")
    for name, limit in (("title", 120), ("reason", 2000), ("prompt", 3800), ("directory", 4096)):
        text = value[name]
        if (not isinstance(text, str) or not text.strip() or len(text.encode("utf-8")) > limit
                or any(ord(char) < 32 and not (name in {"reason", "prompt"} and char in "\n\r\t")
                       or ord(char) == 127 for char in text)):
            raise ValueError(f"Invalid task follow-up {name}")
    validate_filesystem_path(value["directory"])
    if not os.path.isabs(value["directory"]):
        raise ValueError("Follow-up directory must be absolute")
    if value["mode"] not in ("inspect", "workspace") or value["workspace"] not in WORKSPACE_MODES:
        raise ValueError("Invalid task follow-up access")
    if (type(value["network"]) is not bool or type(value["temporary_files"]) is not bool
            or value["mode"] == "inspect" and (value["network"] or value["temporary_files"] or value["workspace"] != "existing")):
        raise ValueError("Invalid task follow-up permissions")
    return value


def private_directory(home: str, directory: Path, *, create: bool = False) -> Path:
    """Check every managed directory component without following symlinks."""
    home_path = Path(os.path.abspath(home))
    validate_filesystem_path(str(directory))
    try:
        parts = directory.relative_to(home_path).parts
    except ValueError as exc:
        raise ValueError("Task storage must remain below the account's home") from exc
    if not parts:
        raise ValueError("Task storage cannot be the account's home")
    current = home_path
    home_info = current.lstat()
    if not stat.S_ISDIR(home_info.st_mode) or home_info.st_uid != os.geteuid() or home_info.st_mode & 0o022:
        raise RuntimeError("Task home must be owned by the account without shared write access")
    for part in parts:
        current /= part
        try:
            info = current.lstat()
        except FileNotFoundError:
            if not create:
                return directory
            current.mkdir(mode=0o700, exist_ok=True)
            info = current.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022:
            raise RuntimeError(f"Unsafe task directory: {current}")
    if directory.stat().st_mode & 0o077:
        raise RuntimeError(f"Task directory must be private: {directory}")
    return directory


def private_json(path: Path, *, max_bytes: int = MAX_CONTEXT_BYTES) -> dict[str, Any]:
    """Read account-owned private JSON, preserving invalid state for inspection."""
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077 or info.st_nlink != 1:
        raise RuntimeError(f"Unsafe task record: {path}")
    value = read_json_file(str(path), max_bytes=max_bytes)
    if not isinstance(value, dict):
        raise RuntimeError(f"Invalid task record: {path}")
    return value


def validate_report(value: object) -> dict[str, Any]:
    """Check semantic output separately from a successful provider exit."""
    required = set(REPORT_SCHEMA["required"]) - {"follow_up"}
    if not isinstance(value, dict) or set(value) not in (required, required | {"follow_up"}):
        raise ValueError("Codex did not return a complete task report")
    validate_follow_up(value.get("follow_up"))
    if value["outcome"] not in OUTCOMES:
        raise ValueError("Invalid task report outcome")
    for name, limit in (("summary", 4000), ("checkpoint", MAX_CHECKPOINT_BYTES)):
        text = value[name]
        if not isinstance(text, str) or len(text.encode("utf-8")) > limit or "\x00" in text:
            raise ValueError(f"Invalid task report {name}")
    evidence = value["evidence"]
    if not isinstance(evidence, list) or len(evidence) > 40 or any(
        not isinstance(item, str) or len(item.encode("utf-8")) > 2000 or "\x00" in item for item in evidence
    ):
        raise ValueError("Invalid task report evidence")
    retry = value["retry_after_minutes"]
    if retry is not None and (type(retry) is not int or not 1 <= retry <= 43200):
        raise ValueError("Invalid task retry interval")
    if value["outcome"] == "complete" and not any(item.strip() for item in evidence):
        raise ValueError("Completion requires recorded verification evidence")
    return value


class CodexEvents:
    """Parse bounded JSONL incrementally; provider output never becomes a command."""

    def __init__(self) -> None:
        self.pending = bytearray()
        self.session_id: str | None = None
        self.report: dict[str, Any] | None = None
        self.usage: dict[str, int] | None = None
        self.final_turn_completed = False
        self.usage_incomplete = False
        self.protocol_error = False
        self.error = ""

    def feed(self, content: bytes, *, final: bool = False) -> None:
        self.pending.extend(content)
        while b"\n" in self.pending:
            line, _, remainder = self.pending.partition(b"\n")
            self.pending = bytearray(remainder)
            self._line(line)
        if len(self.pending) > MAX_EVENT_BYTES:
            self.pending.clear()
            self.error = "Codex event exceeded the record limit"
        if final and self.pending:
            self._line(bytes(self.pending))
            self.pending.clear()

    def _line(self, line: bytes) -> None:
        if len(line) > MAX_EVENT_BYTES:
            self.error = "Codex event exceeded the record limit"
            return
        try:
            event = json.loads(line)
        except (ValueError, UnicodeError, RecursionError):
            return  # CLI diagnostics on stderr are retained in the private log.
        if not isinstance(event, dict):
            return
        if event.get("type") == "thread.started":
            session = event.get("thread_id")
            if isinstance(session, str) and _SESSION.fullmatch(session):
                if self.session_id is not None and self.session_id != session:
                    self.protocol_error = True
                    self.error = "Codex changed conversation IDs during one invocation"
                else:
                    self.session_id = session
        elif event.get("type") == "turn.started":
            self.report = None
            self.final_turn_completed = False
        elif event.get("type") in {"turn.failed", "error"}:
            self.report = None
            self.final_turn_completed = False
            self.usage_incomplete = True
            self.usage = None
            self.error = "Codex reported an unsuccessful turn"
        elif event.get("type") == "turn.completed":
            self.final_turn_completed = True
            usage = event.get("usage")
            if (isinstance(usage, dict)
                    and all(type(usage.get(name)) is int and 0 <= usage[name] <= 10**15
                            for name in ("input_tokens", "output_tokens"))
                    and all(usage[name] >= count for name, count in (self.usage or {}).items())):
                if not self.usage_incomplete:
                    # Codex exec reports cumulative thread totals, including
                    # usage restored on resume; completion events are not additive.
                    self.usage = {name: usage[name] for name in ("input_tokens", "output_tokens")}
            else:
                self.usage_incomplete = True
                self.usage = None
        elif event.get("type") == "item.completed":
            item = event.get("item")
            if isinstance(item, dict) and item.get("type") == "agent_message" and isinstance(item.get("text"), str):
                self.final_turn_completed = False
                try:
                    self.report = validate_report(json.loads(item["text"]))
                    if not self.protocol_error:
                        self.error = ""
                except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
                    self.report = None
                    self.error = str(exc)


class TaskContextStore:
    """Keep resumable state and evidence outside a task's editable workspace."""

    def __init__(self, home: str) -> None:
        self.home = os.path.realpath(home)
        self.root = Path(self.home) / ".local/state/basaltwater/task-contexts"

    def directory(self, identifier: str, *, create: bool = False) -> Path:
        if not isinstance(identifier, str) or not _ID.fullmatch(identifier):
            raise ValueError("Invalid task context ID")
        private_directory(self.home, self.root, create=create)
        return private_directory(self.home, self.root / identifier, create=create)

    def load(self, identifier: str) -> dict[str, Any]:
        directory = self.directory(identifier)
        try:
            value = private_json(directory / "context.json")
        except FileNotFoundError:
            return {"version": 1, "checkpoint": "", "session_id": None, "workspace": None}
        if (not {"version", "checkpoint", "session_id", "workspace"} <= set(value)
                or type(value.get("version")) is not int or value["version"] != 1 or not isinstance(value.get("checkpoint"), str)
                or len(value["checkpoint"].encode("utf-8")) > MAX_CHECKPOINT_BYTES
                or "\x00" in value["checkpoint"]
                or value.get("session_id") is not None and (
                    not isinstance(value["session_id"], str) or not _SESSION.fullmatch(value["session_id"]))
                or value.get("workspace") is not None and not isinstance(value["workspace"], dict)):
            raise RuntimeError(f"Invalid task context: {directory}")
        if "source_directory" in value:
            if not isinstance(value["source_directory"], str):
                raise RuntimeError("Invalid task context source directory")
            validate_filesystem_path(value["source_directory"])
            if not os.path.isabs(value["source_directory"]):
                raise RuntimeError("Invalid task context source directory")
        if "persistence" in value and value["persistence"] not in PERSISTENCE_MODES:
            raise RuntimeError("Invalid task context persistence")
        if "initial_checkpoint_applied" in value and type(value["initial_checkpoint_applied"]) is not bool:
            raise RuntimeError("Invalid task initial checkpoint marker")
        usage = value.get("session_usage")
        if usage is not None and (not isinstance(usage, dict) or set(usage) != {"input_tokens", "output_tokens"}
                                  or any(type(count) is not int or not 0 <= count <= 10**15 for count in usage.values())):
            raise RuntimeError("Invalid task conversation usage baseline")
        if "last_run_id" in value and (not isinstance(value["last_run_id"], str) or not _ID.fullmatch(value["last_run_id"])):
            raise RuntimeError("Invalid task context run ID")
        return value

    def save(self, identifier: str, value: dict[str, Any]) -> None:
        directory = self.directory(identifier, create=True)
        if len((json.dumps(value, indent=2, allow_nan=False) + "\n").encode()) > MAX_CONTEXT_BYTES:
            raise ValueError("Task context exceeds the storage limit")
        write_json_atomic(str(directory / "context.json"), value)

    def prepare(self, task: dict[str, Any], run_id: str) -> dict[str, Any]:
        if not _ID.fullmatch(run_id):
            raise ValueError("Invalid task run ID")
        identifier = task["id"]
        directory = self.directory(identifier, create=True)
        context = self.load(identifier)
        if context.get("persistence", task["persistence"]) != task["persistence"]:
            context.update(checkpoint="", session_id=None)
            context.pop("session_usage", None)
            context.pop("last_run_id", None)
        context["persistence"] = task["persistence"]
        initial_checkpoint = ""
        if not context.get("initial_checkpoint_applied"):
            # Seed only a new context. Older contexts without this marker may
            # already have a session or checkpoint and must retain their work.
            if not context["checkpoint"] and not context["session_id"] and not context.get("last_run_id"):
                initial_checkpoint = task.get("initial_checkpoint", "")
                context["checkpoint"] = initial_checkpoint
            context["initial_checkpoint_applied"] = True
        source = task["directory"]
        validate_filesystem_path(source, must_exist=True)
        if not os.path.isdir(source) or os.path.realpath(source) != source:
            raise RuntimeError("Task source directory moved or changed; review it before continuing")
        if context.get("source_directory", source) != source:
            raise RuntimeError("Task source no longer matches its established context")
        workspace_mode = task["workspace"]
        if workspace_mode == "existing":
            execution_directory = source
        elif workspace_mode in {"directory", "run-directory"}:
            workspace = Path(self.home) / ".local/share/basaltwater/task-workspaces" / identifier
            saved = context.get("workspace")
            if saved is not None:
                if saved.get("mode") != workspace_mode or saved.get("path") != str(workspace):
                    raise RuntimeError("Task directory no longer matches its managed ownership")
                private_directory(self.home, workspace)
                if not workspace.is_dir():
                    raise RuntimeError("Managed task workspace is missing; restore it before continuing")
            private_directory(self.home, workspace, create=True)
            context["workspace"] = {"mode": workspace_mode, "path": str(workspace), "source_directory": source}
            if workspace_mode == "run-directory":
                workspace /= run_id
            execution_directory = str(private_directory(self.home, workspace, create=True))
        else:
            from lib.agent_workspace import create_agent_worktree, inspect_agent_worktree

            saved = context.get("workspace")
            label = "prompt-" + identifier
            if saved is None:
                saved = create_agent_worktree(source, label, home=self.home)
                context["workspace"] = saved
                self.save(identifier, context)  # Persist ownership before an agent can edit it.
            validate_filesystem_path(saved["path"], must_exist=True)
            if os.path.commonpath((os.path.realpath(saved["path"]), self.home)) != self.home:
                raise RuntimeError("Task worktree must remain below the account's home")
            inspected = inspect_agent_worktree(saved["path"])
            if (not inspected.get("managed") or inspected.get("task") != label
                    or inspected.get("repository") != saved["repository"]
                    or inspected.get("branch") != saved["branch"]):
                raise RuntimeError("Task worktree no longer matches its managed ownership")
            execution_directory = saved["path"]
        if task["persistence"] != "fresh" or workspace_mode != "existing":
            context["source_directory"] = source
        self.save(identifier, context)
        runs = private_directory(self.home, directory / "runs", create=True)
        schema = directory / "report-schema.json"
        write_json_atomic(str(schema), REPORT_SCHEMA)
        baseline = {"input_tokens": 0, "output_tokens": 0}
        if task["persistence"] == "conversation" and context["session_id"]:
            baseline = context.get("session_usage") if task.get("last_run_id") == context.get("last_run_id") else None
        return {
            "context_directory": str(directory), "directory": execution_directory,
            "source_directory": source, "schema_path": str(schema),
            "log_path": str(runs / (run_id + ".jsonl")),
            "checkpoint": context["checkpoint"] if task["persistence"] != "fresh" else initial_checkpoint,
            "session_id": context["session_id"] if task["persistence"] == "conversation" else None,
            "usage_baseline": baseline,
        }

    def record(self, task: dict[str, Any], run: dict[str, Any]) -> None:
        identifier = task["id"]
        directory = self.directory(identifier, create=True)
        runs = private_directory(self.home, directory / "runs", create=True)
        receipt = {key: value for key, value in run.items() if key != "task"}
        receipt["task"] = {key: value for key, value in run["task"].items() if not key.startswith("_")}
        # Keep provider evidence even if updating the checkpoint later fails.
        write_json_atomic(str(runs / (run["id"] + ".json")), receipt)
        context = self.load(identifier)
        if task["persistence"] != "fresh":
            if isinstance(run.get("checkpoint"), str):
                context["checkpoint"] = run["checkpoint"]
            if task["persistence"] == "conversation" and run.get("session_id"):
                context["session_id"] = run["session_id"]
            if task["persistence"] == "conversation":
                # An interrupted/failed run can spend tokens without a final
                # total. Invalidate the baseline instead of charging them twice
                # or attributing an unknown amount to a later invocation.
                context["session_usage"] = run.get("session_usage") if run.get("session_id") else None
            context["last_run_id"] = run["id"]
            self.save(identifier, context)
        receipts = sorted((path for path in runs.glob("*.json") if _ID.fullmatch(path.stem)),
                          key=lambda path: path.stat().st_mtime_ns)
        for old in receipts[:-task["history_limit"]]:
            for path in (old, old.with_suffix(".jsonl")):
                try:
                    info = path.lstat()
                except FileNotFoundError:
                    continue
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077 or info.st_nlink != 1:
                    raise RuntimeError(f"Unsafe task evidence: {path}")
                path.unlink()

    def reset(self, identifier: str) -> None:
        context = self.load(identifier)
        context.update(checkpoint="", session_id=None, initial_checkpoint_applied=True)
        context.pop("session_usage", None)
        context.pop("last_run_id", None)
        self.save(identifier, context)

    def receipt(self, identifier: str, run_id: str) -> dict[str, Any]:
        """Read exact retained evidence, including for a deleted task."""
        if not isinstance(run_id, str) or not _ID.fullmatch(run_id):
            raise ValueError("Invalid task run ID")
        runs = private_directory(self.home, self.directory(identifier) / "runs")
        value = private_json(runs / (run_id + ".json"), max_bytes=1024 * 1024)
        if value.get("id") != run_id or not isinstance(value.get("task"), dict) or value["task"].get("id") != identifier:
            raise RuntimeError("Task receipt does not match the requested task and run")
        return value

    def history(self, identifier: str, *, limit: int = 40) -> list[dict[str, Any]]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("History limit must be 1–100")
        runs = private_directory(self.home, self.directory(identifier) / "runs")
        if not runs.exists():
            return []
        retained = []
        for path in runs.glob("*.json"):
            if _ID.fullmatch(path.stem):
                try:
                    retained.append((path.lstat().st_mtime_ns, path))
                except FileNotFoundError:
                    continue  # Retention can also remove records before sorting.
        paths = [path for _, path in sorted(retained, key=lambda item: item[0], reverse=True)[:limit]]
        fields = {"id", "status", "outcome", "started_at", "finished_at", "duration_seconds", "message",
                  "output", "usage", "execution_directory", "context_directory", "log_path", "session_id"}
        history = []
        for path in paths:
            try:
                receipt = self.receipt(identifier, path.stem)
            except FileNotFoundError:
                continue  # Concurrent retention can remove an older record.
            history.append({key: value for key, value in receipt.items() if key in fields})
        return history
