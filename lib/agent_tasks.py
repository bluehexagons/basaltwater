"""Private Codex prompt tasks and a durable, serial panel scheduler."""

from __future__ import annotations

import copy
import fcntl
import json
import math
import os
import re
import selectors
import signal
import stat
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from lib.agent_cli import _tool_path
from lib.atomic_io import write_text_atomic
from lib.validation import validate_filesystem_path


MAX_TASKS = 32
MAX_RUNS = 40
MAX_PROMPT_BYTES = 4000
MAX_OUTPUT_BYTES = 8192
MAX_STATE_BYTES = 1024 * 1024
MAX_STREAM_BYTES = 1024 * 1024
DEFAULT_TIMEOUT_MINUTES = 30
MAX_TIMEOUT_MINUTES = 7 * 24 * 60
DEFAULT_FAILURE_LIMIT = 3
MAX_FAILURE_LIMIT = 10
EFFORTS = ("minimal", "low", "medium", "high", "xhigh", "max", "ultra")
WEB_SEARCH_MODES = ("disabled", "cached", "live")
INTERVALS = {"once": 0, "hourly": 3600, "daily": 86400, "weekly": 604800}
_ID = re.compile(r"[a-f0-9]{32}\Z")
_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,99}\Z")
_STATUSES = {"running", "completed", "failed", "cancelled", "interrupted"}


def _text(value: object, label: str, limit: int, *, multiline: bool = False) -> str:
    if not isinstance(value, str) or len(value.encode("utf-8")) > limit:
        raise ValueError(f"{label} is too long or invalid")
    value = value.strip()
    if not value or any(
        ord(char) < 32 and not (multiline and char in "\n\r\t") or ord(char) == 127
        for char in value
    ):
        raise ValueError(f"{label} is required and must contain printable text")
    return value


def codex_models(home: str) -> list[dict[str, Any]]:
    """Read only public selector metadata from Codex's bounded local cache."""

    path = Path(home) / ".codex/models_cache.json"
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as source:
            metadata = os.fstat(source.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_STATE_BYTES:
                return []
            payload = source.read(MAX_STATE_BYTES + 1)
            if len(payload) > MAX_STATE_BYTES:
                return []
            cache = json.loads(payload)
        models = cache.get("models") if isinstance(cache, dict) else None
        if not isinstance(models, list):
            return []
        result, seen = [], set()
        for model in models[:100]:
            if not isinstance(model, dict) or model.get("visibility") != "list":
                continue
            slug = model.get("slug")
            if not isinstance(slug, str) or not _MODEL.fullmatch(slug) or slug in seen:
                continue
            levels = model.get("supported_reasoning_levels", [])
            if not isinstance(levels, list):
                levels = []
            efforts = [level["effort"] for level in levels if isinstance(level, dict) and level.get("effort") in EFFORTS]
            name = _text(model.get("display_name", slug), "Model label", 120)
            result.append({"slug": slug, "name": name, "efforts": efforts})
            seen.add(slug)
        return result
    except (OSError, ValueError, UnicodeError, RecursionError):
        return []


def _validate_model_effort(task: dict[str, Any], home: str) -> None:
    for model in codex_models(home):
        if model["slug"] == task["model"] and model["efforts"] and task["effort"]:
            if task["effort"] not in model["efforts"]:
                raise ValueError(f"This model's cached effort levels are: {', '.join(model['efforts'])}")


def validate_task(values: dict[str, Any], home: str, *, check_directory: bool = True) -> dict[str, Any]:
    """Validate the finite set of prompt settings; never accept a command line."""

    title = _text(values.get("title"), "Task name", 120)
    prompt = _text(values.get("prompt"), "Prompt", MAX_PROMPT_BYTES, multiline=True)
    directory = _text(values.get("directory"), "Working directory", 4096)
    if directory == "~" or directory.startswith("~/"):
        directory = os.path.join(home, directory[2:]) if directory != "~" else home
    if not os.path.isabs(directory):
        raise ValueError("Working directory must be an absolute path or start with ~/")
    validate_filesystem_path(directory, must_exist=check_directory)
    directory = os.path.realpath(directory)
    if check_directory and not os.path.isdir(directory):
        raise ValueError("Working directory must be a directory")
    mode = values.get("mode")
    if mode not in {"inspect", "workspace"}:
        raise ValueError("Select inspection or workspace changes")
    if mode == "workspace" and os.path.commonpath((directory, home)) != home:
        raise ValueError("Workspace changes must use a directory inside the panel account's home")
    model = values.get("model", "")
    if not isinstance(model, str) or (model and not _MODEL.fullmatch(model)):
        raise ValueError("Model name is invalid")
    effort = values.get("effort", "")
    if not isinstance(effort, str) or (effort and effort not in EFFORTS):
        raise ValueError("Select a supported reasoning effort")
    timeout = values.get("timeout_minutes", DEFAULT_TIMEOUT_MINUTES)
    if isinstance(timeout, str) and re.fullmatch(r"[0-9]{1,5}", timeout):
        timeout = int(timeout)
    if type(timeout) is not int or not 1 <= timeout <= MAX_TIMEOUT_MINUTES:
        raise ValueError(f"Maximum runtime must be 1–{MAX_TIMEOUT_MINUTES} whole minutes")
    failure_limit = values.get("failure_limit", DEFAULT_FAILURE_LIMIT)
    if isinstance(failure_limit, str) and re.fullmatch(r"[0-9]{1,2}", failure_limit):
        failure_limit = int(failure_limit)
    if type(failure_limit) is not int or not 0 <= failure_limit <= MAX_FAILURE_LIMIT:
        raise ValueError(f"Pause after failures must be 0–{MAX_FAILURE_LIMIT} whole runs")
    web_search = values.get("web_search", "disabled")
    if not isinstance(web_search, str) or web_search not in WEB_SEARCH_MODES:
        raise ValueError("Select disabled, cached, or live web search")
    session_history = values.get("session_history", False)
    if type(session_history) is not bool:
        raise ValueError("Invalid Codex session history option")
    temporary_files = values.get("temporary_files", False)
    if type(temporary_files) is not bool or (temporary_files and mode != "workspace"):
        raise ValueError("Temporary file writes require workspace changes")
    interval = values.get("interval")
    if not isinstance(interval, str) or interval not in INTERVALS:
        raise ValueError("Select a supported repeat interval")
    network = values.get("network", False)
    if not isinstance(network, bool) or (network and mode != "workspace"):
        raise ValueError("Command network access requires workspace changes")
    return {"title": title, "prompt": prompt, "directory": directory, "mode": mode,
            "model": model, "effort": effort, "timeout_minutes": timeout, "failure_limit": failure_limit,
            "web_search": web_search, "session_history": session_history, "temporary_files": temporary_files,
            "interval": interval, "network": network}


def codex_command(task: dict[str, Any], executable: str) -> list[str]:
    """Build an argument vector; prompt text travels only through stdin."""

    command = [executable, "-c", 'approval_policy="never"', "exec", "--color", "never",
               "--sandbox", "read-only" if task["mode"] == "inspect" else "workspace-write",
               "--skip-git-repo-check"]
    if task["mode"] == "workspace":
        command += ["-c", "sandbox_workspace_write.network_access=" + str(task["network"]).lower()]
        # Do not inherit additional writable roots; temporary grants are explicit.
        command += ["-c", "sandbox_workspace_write.writable_roots=[]",
                    "-c", "sandbox_workspace_write.exclude_tmpdir_env_var=" + str(not task["temporary_files"]).lower(),
                    "-c", "sandbox_workspace_write.exclude_slash_tmp=" + str(not task["temporary_files"]).lower()]
    if task["model"]:
        command += ["--model", task["model"]]
    if task["effort"]:
        command += ["-c", f'model_reasoning_effort="{task["effort"]}"']
    command += ["-c", f'web_search="{task["web_search"]}"']
    if not task["session_history"]:
        command += ["--ephemeral"]
    return command + ["-"]


def _kill_group(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def execute_prompt(task: dict[str, Any], home: str, cancel: threading.Event) -> dict[str, Any]:
    """Execute one bounded prompt without a shell, root, or approval bypass."""

    if os.geteuid() == 0:
        raise RuntimeError("Prompt execution requires a non-root account")
    task = validate_task(task, home)
    timeout_seconds = task["timeout_minutes"] * 60
    timeout_message = f"Run exceeded the {task['timeout_minutes']}-minute limit"
    executable = _tool_path("codex", home)
    if not executable:
        raise RuntimeError("Codex is not installed for the panel account")
    environment = os.environ.copy()
    environment["HOME"] = home
    environment["PATH"] = os.pathsep.join((os.path.join(home, ".local", "bin"),
                                          environment.get("PATH", "/usr/local/bin:/usr/bin:/bin")))
    # A panel task is a new terminal session, independent of the host UI's thread.
    for key in ("CODEX_THREAD_ID", "CODEX_TURN_ID", "BASALTWATER_T3_LOGINCTL_SHIM"):
        environment.pop(key, None)
    instruction = (
        "This is an unattended Basaltwater task. Follow the working directory's agent instructions. "
        "Report what you checked, changed, and validated. Do not publish, push, or deploy unless "
        "this prompt explicitly requests it. Privileged host changes require the configured "
        "approval mechanism; report blocked work.\n\n" + task["prompt"]
    ).encode("utf-8")
    output = bytearray()
    received = 0
    status = "completed"
    reason = ""
    started = time.monotonic()
    with subprocess.Popen(
        codex_command(task, executable), cwd=task["directory"], env=environment,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        start_new_session=True,
    ) as process:
        try:
            assert process.stdin is not None and process.stdout is not None
            process.stdin.write(instruction)
            process.stdin.close()
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while selector.get_map():
                    if cancel.is_set() or time.monotonic() - started >= timeout_seconds:
                        status = "cancelled" if cancel.is_set() else "failed"
                        reason = "Cancelled by operator" if cancel.is_set() else timeout_message
                        _kill_group(process)
                        break
                    for key, _ in selector.select(timeout=0.5):
                        chunk = os.read(key.fd, 4096)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        received += len(chunk)
                        output.extend(chunk)
                        del output[:-MAX_OUTPUT_BYTES]
                        if received > MAX_STREAM_BYTES:
                            status, reason = "failed", "Run exceeded the output limit"
                            _kill_group(process)
                            break
                    if reason:
                        break
            # Kill remaining descendants even if Codex closed stdout before exiting.
            while True:
                if cancel.is_set() or time.monotonic() - started >= timeout_seconds:
                    status = "cancelled" if cancel.is_set() else "failed"
                    reason = "Cancelled by operator" if cancel.is_set() else timeout_message
                    _kill_group(process)
                try:
                    returncode = process.wait(timeout=0.5)
                    break
                except subprocess.TimeoutExpired:
                    continue
        finally:
            _kill_group(process)
        if returncode and status == "completed":
            status, reason = "failed", f"Codex exited with code {returncode}"
    # Replacement characters for malformed bytes can expand the UTF-8 size.
    text = output.decode("utf-8", errors="replace").encode("utf-8")[-MAX_OUTPUT_BYTES:].decode("utf-8", errors="ignore")
    if received > MAX_OUTPUT_BYTES:
        text = "… earlier output omitted …\n" + text
    return {"status": status, "output": text, "message": reason or "Codex completed",
            "exit_code": returncode}


class AgentTasks:
    """Persist prompts and history; execute one job at a time while the panel runs."""

    def __init__(self, home: str | None = None) -> None:
        self.home = os.path.realpath(home or os.path.expanduser("~"))
        self.parent = Path(self.home) / ".local/state/basaltwater/prompt-tasks"
        self.path = self.parent / "tasks.json"
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self._lease: int | None = None
        self._state: dict[str, Any] | None = None
        self.error = ""

    def available(self) -> bool:
        try:
            return os.geteuid() != 0 and os.path.isdir(self.home) and os.stat(self.home).st_uid == os.geteuid()
        except OSError:
            return False

    def _check_parent(self, *, create: bool = False) -> None:
        if not self.available():
            raise RuntimeError("Prompt tasks require a non-root panel account with its own home directory")
        validate_filesystem_path(str(self.parent))
        if os.path.commonpath((os.path.realpath(self.parent), self.home)) != self.home:
            raise RuntimeError("Prompt task storage must remain inside the account's home")
        if create:
            self.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.parent.exists():
            metadata = self.parent.lstat()
            if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.geteuid() or metadata.st_mode & 0o077:
                raise RuntimeError("Prompt task directory must be private and owned by the panel account")

    def _load(self) -> dict[str, Any]:
        if self._state is not None:
            return self._state
        self._check_parent()
        try:
            descriptor = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            self._state = {"version": 1, "tasks": [], "runs": []}
            return self._state
        with os.fdopen(descriptor, "rb") as source:
            metadata = os.fstat(source.fileno())
            if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.geteuid()
                    or metadata.st_mode & 0o077 or metadata.st_size > MAX_STATE_BYTES):
                raise RuntimeError("Prompt task state permissions or size are invalid")
            try:
                payload = source.read(MAX_STATE_BYTES + 1)
                if len(payload) > MAX_STATE_BYTES:
                    raise ValueError("State exceeds size limit")
                value = json.loads(payload)
            except (ValueError, UnicodeError, RecursionError) as exc:
                raise RuntimeError("Prompt task state is invalid") from exc
        self._validate_state(value)
        self._state = value
        return value

    def _validate_state(self, value: Any) -> None:
        if (not isinstance(value, dict) or value.get("version") != 1
                or not isinstance(value.get("tasks"), list) or len(value["tasks"]) > MAX_TASKS
                or not isinstance(value.get("runs"), list) or len(value["runs"]) > MAX_RUNS):
            raise RuntimeError("Prompt task state has an unsupported schema")
        try:
            ids: set[str] = set()
            for task in value["tasks"]:
                if not isinstance(task, dict):
                    raise ValueError("Invalid task")
                task.update(validate_task(task, self.home, check_directory=False))
                if not _ID.fullmatch(task["id"]) or task["id"] in ids:
                    raise ValueError("Invalid task ID")
                ids.add(task["id"])
                if type(task["enabled"]) is not bool or type(task["queued"]) is not bool:
                    raise ValueError("Invalid task state")
                task.setdefault("draft", False)
                task.setdefault("auto_paused", False)
                task.setdefault("consecutive_failures", 0)
                if (type(task["draft"]) is not bool or type(task["auto_paused"]) is not bool
                        or type(task["consecutive_failures"]) is not int
                        or not 0 <= task["consecutive_failures"] <= 1000000
                        or task["draft"] and (task["enabled"] or task["queued"])
                        or task["auto_paused"] and task["enabled"]):
                    raise ValueError("Invalid task failure or draft state")
                if task["enabled"] and not INTERVALS[task["interval"]]:
                    raise ValueError("One-time task cannot repeat")
                due = task["next_run"]
                if due is not None and (type(due) not in {float, int} or not math.isfinite(due)):
                    raise ValueError("Invalid due time")
                if task["enabled"] and due is None:
                    raise ValueError("Missing due time")
            for run in value["runs"]:
                if not isinstance(run, dict) or not _ID.fullmatch(run["id"]) or run["status"] not in _STATUSES:
                    raise ValueError("Invalid run")
                if not isinstance(run["task"], dict):
                    raise ValueError("Invalid run task")
                run["task"].update(validate_task(run["task"], self.home, check_directory=False))
                if not _ID.fullmatch(run["task"]["id"]) or not isinstance(run["message"], str) or len(run["message"]) > 500:
                    raise ValueError("Invalid run metadata")
                for name in ("started_at", "finished_at"):
                    timestamp = run[name]
                    if timestamp is None and name == "finished_at":
                        continue
                    if type(timestamp) not in {int, float} or not math.isfinite(timestamp):
                        raise ValueError("Invalid run time")
                if not isinstance(run["output"], str) or len(run["output"].encode("utf-8")) > MAX_OUTPUT_BYTES + 128:
                    raise ValueError("Invalid output")
                duration = run.get("duration_seconds")
                if duration is not None and (type(duration) not in {int, float} or not math.isfinite(duration) or duration < 0):
                    raise ValueError("Invalid run duration")
                exit_code = run.get("exit_code")
                if exit_code is not None and type(exit_code) is not int:
                    raise ValueError("Invalid run exit code")
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("Prompt task state is invalid") from exc

    def _save(self) -> None:
        try:
            self._check_parent(create=True)
            assert self._state is not None
            content = json.dumps(self._state, ensure_ascii=False) + "\n"
            if len(content.encode("utf-8")) > MAX_STATE_BYTES:
                raise RuntimeError("Prompt task storage limit reached")
            write_text_atomic(str(self.path), content, mode=0o600)
        except Exception:
            self._state = None
            raise

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._load())

    def create(self, values: dict[str, Any], *, run_now: bool, now: float | None = None, draft: bool = False) -> str:
        task = validate_task(values, self.home)
        _validate_model_effort(task, self.home)
        if type(draft) is not bool or draft and run_now:
            raise ValueError("A draft cannot run immediately")
        if not draft and not _tool_path("codex", self.home):
            raise ValueError("Install Codex for the panel account before creating prompt tasks")
        interval = INTERVALS[task["interval"]]
        if not interval and not run_now and not draft:
            raise ValueError("A one-time prompt must be run now")
        with self._lock:
            state = self._load()
            if len(state["tasks"]) >= MAX_TASKS:
                raise ValueError(f"At most {MAX_TASKS} saved tasks are allowed; remove an unused task")
            identifier = uuid.uuid4().hex
            state["tasks"].append({**task, "id": identifier, "enabled": bool(interval) and not draft,
                                   "queued": run_now, "draft": draft, "auto_paused": False, "consecutive_failures": 0,
                                   "next_run": (now if now is not None else time.time()) + interval if interval and not draft else None})
            self._save()
        self._wake.set()
        return identifier

    def update(self, identifier: str, values: dict[str, Any], *, now: float | None = None) -> None:
        task_values = validate_task(values, self.home)
        _validate_model_effort(task_values, self.home)
        if not _ID.fullmatch(identifier):
            raise ValueError("Invalid prompt task ID")
        with self._lock:
            state = self._load()
            task = next((task for task in state["tasks"] if task["id"] == identifier), None)
            if task is None:
                raise ValueError("Prompt task was not found")
            if task["queued"] or any(run["task"]["id"] == identifier and run["status"] == "running" for run in state["runs"]):
                raise ValueError("Wait for or cancel the active run before editing its task")
            interval = INTERVALS[task_values["interval"]]
            changed_interval = task["interval"] != task_values["interval"]
            task.update(task_values)
            if not interval:
                task["enabled"], task["next_run"] = False, None
            elif changed_interval:
                task["next_run"] = (time.time() if now is None else now) + interval
            self._save()
        self._wake.set()

    def action(self, identifier: str, action: str, *, now: float | None = None) -> None:
        if not _ID.fullmatch(identifier) or action not in {"run", "pause", "resume", "delete", "cancel"}:
            raise ValueError("Invalid prompt task action")
        with self._lock:
            state = self._load()
            task = next((task for task in state["tasks"] if task["id"] == identifier), None)
            if task is None:
                raise ValueError("Prompt task was not found")
            running = any(run["task"]["id"] == identifier and run["status"] == "running" for run in state["runs"])
            if action == "cancel":
                task["queued"] = False
                if running:
                    self._cancel.set()
            elif action == "delete":
                if running:
                    raise ValueError("Cancel the running task before removing it")
                state["tasks"].remove(task)
            elif action == "run":
                if running or task["queued"]:
                    raise ValueError("This task is already running or queued")
                task["queued"], task["draft"] = True, False
            elif action == "pause":
                task["enabled"], task["queued"] = False, False
                task["auto_paused"] = False
            else:
                interval = INTERVALS[task["interval"]]
                if not interval:
                    raise ValueError("A one-time task cannot be resumed as a schedule")
                task["enabled"], task["draft"], task["auto_paused"] = True, False, False
                task["consecutive_failures"] = 0
                task["next_run"] = (time.time() if now is None else now) + interval
            self._save()
        self._wake.set()

    def _record_outcome(self, state: dict[str, Any], run: dict[str, Any]) -> None:
        task = next((task for task in state["tasks"] if task["id"] == run["task"]["id"]), None)
        if task is None:
            return
        if run["status"] == "completed":
            task["consecutive_failures"], task["auto_paused"] = 0, False
        elif run["status"] in {"failed", "interrupted"}:
            task["consecutive_failures"] = min(1000000, task["consecutive_failures"] + 1)
            if task["enabled"] and task["failure_limit"] and task["consecutive_failures"] >= task["failure_limit"]:
                task["enabled"], task["queued"], task["auto_paused"] = False, False, True

    def tick(self, *, now: float | None = None) -> bool:
        """Claim one due task, advance its deadline, then execute outside the lock."""

        current = time.time() if now is None else now
        with self._lock:
            state = self._load()
            if any(run["status"] == "running" for run in state["runs"]):
                return False
            # Explicit run-now requests take priority over recurring work.
            task = next((task for task in state["tasks"] if task["queued"]), None)
            if task is None:
                task = next((task for task in state["tasks"] if task["enabled"] and task["next_run"] <= current), None)
            if task is None:
                return False
            task["queued"] = False
            if task["enabled"] and task["next_run"] <= current:
                interval = INTERVALS[task["interval"]]
                task["next_run"] += (int((current - task["next_run"]) // interval) + 1) * interval
            run = {"id": uuid.uuid4().hex, "task": copy.deepcopy(task), "status": "running",
                   "started_at": current, "finished_at": None, "output": "", "message": "Codex is running"}
            state["runs"] = (state["runs"] + [run])[-MAX_RUNS:]
            self._cancel.clear()
            self._save()
        started = time.monotonic()
        try:
            result = execute_prompt(run["task"], self.home, self._cancel)
        except Exception as exc:
            result = {"status": "failed", "output": "", "message": f"Prompt could not complete: {type(exc).__name__}: {exc}"[:500]}
        with self._lock:
            state = self._load()
            stored_run = next(record for record in state["runs"] if record["id"] == run["id"])
            stored_run.update(result, finished_at=time.time(), duration_seconds=max(0, time.monotonic() - started))
            self._record_outcome(state, stored_run)
            self._save()
        return True

    def start(self) -> None:
        """Take a lifetime process lock before recovering and scheduling tasks."""

        if self._thread is not None or not self.available():
            return
        try:
            self._check_parent(create=True)
            lease = os.open(self.parent / "worker.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
            metadata = os.fstat(lease)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.geteuid() or metadata.st_mode & 0o077:
                os.close(lease)
                raise RuntimeError("Prompt scheduler lock is unsafe")
            try:
                fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                os.close(lease)
                raise RuntimeError("Another prompt scheduler is already running")
            self._lease = lease
            with self._lock:
                state = self._load()
                for run in state["runs"]:
                    if run["status"] == "running":
                        finished = time.time()
                        run.update(status="interrupted", finished_at=finished,
                                   duration_seconds=max(0, finished - run["started_at"]),
                                   message="Panel restarted during this run; review changes before running it again")
                        self._record_outcome(state, run)
                self._save()
            self._thread = threading.Thread(target=self._loop, daemon=True, name="agent-prompts")
            self._thread.start()
        except (OSError, RuntimeError, ValueError) as exc:
            self.error = str(exc)
            self.close()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                if self.tick():
                    continue
            except Exception as exc:
                self.error = f"Scheduler stopped: {type(exc).__name__}: {exc}"
                return
            self._wake.wait(timeout=15)
            self._wake.clear()

    def close(self) -> None:
        self._stop.set()
        self._cancel.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=6)
        if self._lease is not None:
            os.close(self._lease)
            self._lease = None
