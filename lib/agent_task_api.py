"""Shared task operations for terminal agents, the panel and stdio MCP."""

from __future__ import annotations

import fcntl
import os
import stat
from pathlib import Path
from typing import Any

from lib.agent_tasks import AgentTasks
from lib.agent_task_storage import STORAGE_OPERATIONS
from lib.atomic_io import read_json_file
from lib.t3code_runtime import is_t3_service_version


WORKER_MARKER = "# Managed by basaltwater agent tasks"


def worker_unit(home: str) -> str:
    return f"basaltwater-agent-tasks-{os.stat(home).st_uid}.service"


def worker_installed(home: str) -> bool:
    path = Path("/etc/systemd/system") / worker_unit(home)
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022 or info.st_size > 16384:
            return False
        return path.read_text().startswith(WORKER_MARKER + "\n")
    except (OSError, UnicodeError):
        return False


def worker_status(manager: AgentTasks) -> dict[str, Any]:
    """A live lease proves worker ownership; unit presence alone does not."""
    if not manager.available():
        return {"installed": False, "running": False, "unit": None,
                "error": "A non-root account with its own home is required"}
    installed = worker_installed(manager.home)
    path = manager.parent / "worker.lock"
    running = False
    error = manager.error or None
    try:
        manager._check_parent()
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(descriptor)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                    or info.st_mode & 0o077 or info.st_nlink != 1):
                raise RuntimeError("Unsafe task worker lease")
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                running = True
        finally:
            os.close(descriptor)
    except FileNotFoundError:
        pass
    except (OSError, RuntimeError, ValueError) as exc:
        error = f"Task worker status unavailable: {exc}"[:500]
    return {"installed": installed, "running": running, "unit": worker_unit(manager.home),
            "error": error}


def task_capabilities(manager: AgentTasks) -> dict[str, Any]:
    runtime = Path(manager.home) / ".t3/runtime"
    version = None
    try:
        state = read_json_file(str(runtime / "service-state.json"), max_bytes=64 * 1024)
        if isinstance(state, dict) and is_t3_service_version(state.get("activeVersion")):
            version = state["activeVersion"]
    except (OSError, ValueError):
        pass
    return {"version": 1, "available": manager.available(), "executors": ["codex"],
            "storage_operations": list(STORAGE_OPERATIONS),
            "persistence": ["fresh", "checkpoint", "conversation"],
            "workspaces": ["existing", "directory", "worktree", "run-directory"],
            "triggers": ["interval", "manual", "event", "scheduled-once"], "worker": worker_status(manager),
            "t3": {"installed": runtime.is_dir(), "active_version": version,
                   "native_task_execution": False, "native_thread_binding": False,
                   "reason": "Native T3 scheduling and thread APIs await stable-release qualification"}}


class TaskAPI:
    """Use the scheduler's transactions rather than editing its state directly."""

    def __init__(self, manager: AgentTasks | None = None) -> None:
        self.manager = manager or AgentTasks()

    def call(self, operation: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(arguments, dict):
            raise ValueError("Task arguments must be an object")
        allowed = {
            "capabilities": set(), "list": {"summary", "history_limit"}, "show": {"id"}, "history": {"id", "limit"},
            "receipt": {"id", "run_id"},
            "create": {"task", "run_now", "draft", "request_key"}, "update": {"id", "task"},
            "action": {"id", "action"}, "notify": {"id", "message", "key"},
            "feedback": {"sender", "message"},
        }
        if operation not in allowed or set(arguments) - allowed[operation]:
            raise ValueError("Unsupported task operation or argument")
        if operation == "capabilities":
            return task_capabilities(self.manager)
        if operation == "feedback":
            from lib.agent_inbox import AgentInbox

            return AgentInbox(self.manager.home).feedback(arguments.get("sender"), arguments.get("message"))
        if operation == "list":
            summary, limit = arguments.get("summary", False), arguments.get("history_limit", 40)
            if type(summary) is not bool or type(limit) is not int or not 0 <= limit <= 40:
                raise ValueError("List summary must be a boolean and history limit must be 0–40")
            state = self.manager.snapshot()
            state["runs"] = state["runs"][-limit:] if limit else []
            if summary:
                fields = {"id", "title", "directory", "interval", "persistence", "workspace", "executor", "start_at",
                          "enabled", "queued", "draft", "auto_paused", "next_run", "not_before", "attempts",
                          "tokens_used", "consecutive_failures", "stop_reason", "max_runs", "deadline_at", "token_budget", "last_run_id",
                          "source_task_id", "source_run_id", "keep", "storage_operation"}
                state["tasks"] = [{key: value for key, value in task.items() if key in fields} for task in state["tasks"]]
                run_fields = {"id", "status", "outcome", "started_at", "finished_at", "duration_seconds", "usage", "summary"}
                compact = []
                for run in state["runs"]:
                    status = None
                    if run.get("follow_up"):
                        from lib.agent_task_followups import follow_up_state

                        try:
                            status = follow_up_state(self.manager, run["task"]["id"], run["id"])["status"]
                        except (OSError, RuntimeError, ValueError):
                            status = "unavailable"
                    compact.append({**{key: value for key, value in run.items() if key in run_fields},
                                    "follow_up_requested": bool(run.get("follow_up")), "follow_up_status": status,
                                    "task": {key: run["task"][key] for key in ("id", "title")}})
                state["runs"] = compact
            return {**state, "worker": worker_status(self.manager)}
        if operation == "create":
            task = arguments.get("task")
            if not isinstance(task, dict):
                raise ValueError("Task settings must be an object")
            from lib.agent_tasks import validate_task

            if set(task) - set(validate_task(task, self.manager.home)):
                raise ValueError("Unknown task settings")
            run_now = arguments.get("run_now", False)
            draft = arguments.get("draft", not run_now)
            if type(run_now) is not bool or type(draft) is not bool:
                raise ValueError("Run and draft settings must be booleans")
            identifier = self.manager.create(task, run_now=run_now, draft=draft,
                                             request_key=arguments.get("request_key", ""))
            return {"id": identifier, "worker": worker_status(self.manager)}
        identifier = arguments.get("id")
        if not isinstance(identifier, str):
            raise ValueError("Task ID is required")
        if operation == "receipt":
            return {"run": self.manager.contexts.receipt(identifier, arguments.get("run_id"))}
        if operation == "history" and self.manager.contexts.directory(identifier).is_dir():
            return {"runs": self.manager.contexts.history(identifier, limit=arguments.get("limit", 40))}
        state = self.manager.snapshot()
        task = next((item for item in state["tasks"] if item["id"] == identifier), None)
        if task is None:
            raise ValueError("Prompt task was not found")
        if operation == "show":
            return {"task": task, "context": self.manager.context_snapshot(identifier),
                    "worker": worker_status(self.manager)}
        if operation == "history":
            return {"runs": self.manager.contexts.history(identifier, limit=arguments.get("limit", 40))}
        if operation == "update":
            settings = arguments.get("task")
            if not isinstance(settings, dict):
                raise ValueError("Task settings must be an object")
            from lib.agent_tasks import validate_task

            if set(settings) - set(validate_task(task, self.manager.home)):
                raise ValueError("Unknown task settings")
            self.manager.update(identifier, settings, partial=True)
        elif operation == "action":
            self.manager.action(identifier, arguments.get("action"))
        elif operation == "notify":
            return {"id": identifier, "accepted": self.manager.notify(identifier, arguments.get("message"),
                                                                      key=arguments.get("key", ""))}
        return {"id": identifier, "worker": worker_status(self.manager)}
