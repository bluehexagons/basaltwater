"""Project-aware tasks owned by the existing desktop user's systemd manager."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import pwd
import re
import secrets
import shlex
import shutil
import stat
import subprocess
import sys
import time

from desktop.browser_session import BROWSERS, browser_command, create_browser_profile, validate_browser_url
from desktop.receipts import DesktopRequestError
from lib.atomic_io import read_json_file, write_json_atomic
from lib.cachyos import is_cachyos
from lib.cachyos_doctor import _owned_socket, _probe
from lib.remote_utils import is_dry_run
from lib.validation import validate_filesystem_path, validate_no_control_characters


LOG_LIMIT = 16 * 1024 * 1024
TASK_ID = re.compile(r"[0-9a-f]{32}")
UNIT_PREFIX = "basaltwater-development-"
CHECK_KINDS = ("check", "compare")
KINDS = ("editor", "run", "import", "exec", "node", *CHECK_KINDS, "playtest", "browser")


class _DevelopmentArgumentParser(argparse.ArgumentParser):
    def __init__(self, *args, script_arguments: bool = False, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.script_arguments = script_arguments

    def parse_known_args(self, args=None, namespace=None) -> tuple[argparse.Namespace, list[str]]:
        arguments = list(sys.argv[1:] if args is None else args)
        if not self.script_arguments or "--" not in arguments:
            return super().parse_known_args(arguments, namespace)
        # Python 3.11 cannot resume a '*' positional after intervening options.
        # Parse only the CLI prefix so script flags and further '--' stay literal.
        separator = arguments.index("--")
        parsed, unknown = super().parse_known_args(arguments[:separator], namespace)
        parsed.argv.extend(arguments[separator + 1:])
        return parsed, unknown


def add_development_parser(commands: argparse._SubParsersAction) -> None:
    from lib.agent_visuals import add_check_options, add_revision_options

    parser = commands.add_parser("develop", help="Check projects and supervise native development tasks without screen control")
    actions = parser.add_subparsers(dest="development_command", required=True,
                                   parser_class=_DevelopmentArgumentParser)
    for name in ("doctor", "editor", "run", "import", "exec"):
        action = actions.add_parser(name)
        action.add_argument("--project", required=True, help="Existing project or worktree directory")
        action.add_argument("--json", action="store_true")
        if name != "exec":
            action.add_argument("--engine", help="Godot executable name or absolute path; defaults to godot or godot-mono for C#")
        if name == "doctor":
            action.add_argument("--export-check", action="store_true", help="Probe the exact Godot version and installed templates without exporting")
        if name != "doctor":
            action.add_argument("--dry-run", action="store_true", help="Validate and show the task without creating state or launching")
        if name in ("editor", "run"):
            action.add_argument("--headless", action="store_true", help="Use Godot's headless display/audio; this does not verify GPU or input")
            action.add_argument("--quit-after", type=int, help="Quit after 1–1000000 engine iterations; this is not a wall-clock deadline")
            action.add_argument("--rendering-method", choices=("forward_plus", "mobile", "gl_compatibility"), help="Explicit per-launch renderer override")
        if name == "run":
            action.add_argument("--scene", help="Existing .tscn/.scn within the project; accepts res:// paths")
            action.add_argument("argv", nargs=argparse.REMAINDER, help="Project arguments following --")
        elif name == "exec":
            action.add_argument("argv", nargs=argparse.REMAINDER, help="Explicit application/project command following --")
    workflows = {"node": "Run a declared package script",
                 "check": "Run a declared project rendering/test recipe in the existing desktop session",
                 "compare": "Compare the same declared recipe across two isolated revisions in the existing desktop session"}
    for name, help_text in workflows.items():
        action = actions.add_parser(name, script_arguments=(name == "node"), help=help_text)
        action.add_argument("script" if name == "node" else "recipe")
        action.add_argument("--project", required=True)
        action.add_argument("--json", action="store_true")
        action.add_argument("--dry-run", action="store_true")
        if name == "node":
            action.add_argument("--manager", choices=("npm", "pnpm", "yarn"), help="Override packageManager; defaults to npm when undeclared")
            action.add_argument("argv", nargs="*", help="Script arguments following --")
        else:
            add_check_options(action)
            if name == "compare":
                add_revision_options(action)
    playtest = actions.add_parser("playtest", help="Launch a declared disposable profile and attach it to the active recorder")
    playtest.add_argument("playtest", metavar="PROFILE")
    playtest.add_argument("--project", required=True)
    playtest.add_argument("--json", action="store_true")
    playtest.add_argument("--dry-run", action="store_true", help="Validate the profile without creating state or launching")
    browser = actions.add_parser("browser", help="Launch a native browser with a private profile and download directory")
    browser.add_argument("--project", required=True, help="Tag the task with an existing project or worktree")
    browser.add_argument("--browser", choices=BROWSERS, default="firefox")
    browser.add_argument("--url", default="about:blank", help="Initial HTTP(S) URL")
    browser.add_argument("--json", action="store_true")
    browser.add_argument("--dry-run", action="store_true", help="Validate without creating a profile or launching")
    cleanup = actions.add_parser("cleanup-browser", help="Remove the profile of a finished browser task")
    cleanup.add_argument("task", help="Task ID returned by develop browser")
    cleanup.add_argument("--include-downloads", action="store_true", help="Also remove the task's private download directory")
    cleanup.add_argument("--dry-run", action="store_true")
    cleanup.add_argument("--json", action="store_true")
    logs = actions.add_parser("logs", help="Show the last lines of a development task's retained output")
    logs.add_argument("task", help="Task ID returned by a development launch")
    logs.add_argument("--tail", type=int, default=30, metavar="LINES", help="Number of lines to show (1–10000, default 30)")
    logs.add_argument("--worker", action="store_true", help="Show supervisor output even when a recipe log is available")
    logs.add_argument("--json", action="store_true")
    for name in ("status", "stop", "wait"):
        action = actions.add_parser(name)
        action.add_argument("task", help="Task ID returned by a development launch")
        action.add_argument("--json", action="store_true")
        if name == "stop":
            action.add_argument("--dry-run", action="store_true", help="Validate the task identity without stopping it")
        elif name == "wait":
            action.add_argument("--timeout", type=int, default=600, help="Wait deadline in seconds (1–3600); does not stop the task")
    listing = actions.add_parser("list", help="List private task records; use status for current process state")
    listing.add_argument("--project", help="Filter records to an existing project/worktree")
    listing.add_argument("--since", help="Inclusive creation-time lower bound, ISO 8601 with timezone")
    listing.add_argument("--until", help="Inclusive creation-time upper bound, ISO 8601 with timezone")
    listing.add_argument("--json", action="store_true")


def _guard() -> int:
    uid = os.getuid()
    if uid == 0 or os.geteuid() != uid:
        raise RuntimeError("Run as the existing desktop account, without sudo")
    if not is_cachyos():
        from desktop.session_runtime import configuration
        configuration()  # Debian inventory uses the configured non-root owner.
    return uid


def _project(value: str) -> Path:
    validate_filesystem_path(value)
    path = Path(value).expanduser().resolve()
    validate_filesystem_path(str(path), must_exist=True)
    if not path.is_dir():
        raise ValueError("Project must be an existing directory")
    return path


def _regular_text(path: Path, *, limit: int = 1024 * 1024) -> str:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("Project metadata must be a regular file")
        body = stream.read(limit + 1)
    if len(body) > limit:
        raise ValueError("Project metadata exceeds its size limit")
    return body.decode("utf-8")


def _godot_features(body: str) -> list[str]:
    sections = re.split(r"(?m)^[ \t]*\[([^\]\r\n]+)\][ \t]*(?:;[^\n]*)?\r?$", body)
    applications = [sections[index + 1] for index in range(1, len(sections), 2)
                    if sections[index] == "application"]
    if len(applications) > 1:
        raise ValueError("Ambiguous Godot application sections")
    if not applications:
        return []
    declarations = list(re.finditer(r"(?m)^[ \t]*config/features[ \t]*=", applications[0]))
    if not declarations:
        return []
    if len(declarations) != 1:
        raise ValueError("Ambiguous Godot application config/features")
    # Keep quoted parentheses/escapes intact without evaluating Godot variants.
    declaration = re.match(
        r'\s*PackedStringArray\(((?:[ \t\r\n,]|"(?:\\.|[^"\\])*")*)\)[ \t]*(?:;[^\n]*)?(?=\r?\n|\Z)',
        applications[0][declarations[0].end():], re.DOTALL)
    if declaration is None:
        raise ValueError("Godot application config/features requires PackedStringArray of strings")
    try:
        values = json.loads("[" + declaration[1].rstrip().removesuffix(",") + "]")
    except ValueError as exc:
        raise ValueError("Invalid Godot application config/features strings") from exc
    if any(not isinstance(value, str) for value in values):
        raise ValueError("Godot application config/features requires strings")
    return values


def project_info(project: Path, engine: str | None = None) -> dict:
    """Read declarations and discover executables; never import or run project code."""
    path = project / "project.godot"
    result = {"directory": str(project), "kind": "native", "missing_tools": [], "runtime_readiness": "unverified"}
    if not path.exists() and not path.is_symlink():
        if (project / "package.json").exists() or (project / "package.json").is_symlink():
            from desktop.development_workflows import package_info
            result["kind"] = "node"
            result["node"] = package_info(project)
        return result
    body = _regular_text(path)
    values = _godot_features(body)
    minimum = next((value for value in values if re.fullmatch(r"[0-9]+\.[0-9]+(?:\.[0-9]+)?", value)), None)
    uses_dotnet = "C#" in values
    executable = _executable(engine or ("godot-mono" if uses_dotnet else "godot"), required=False)
    result.update(kind="godot", engine=executable, minimum_engine_version=minimum,
                  engine_version="unverified", requires_dotnet=uses_dotnet,
                  dotnet=shutil.which("dotnet") if uses_dotnet else None)
    if not executable:
        result["missing_tools"].append("Godot .NET engine" if uses_dotnet else "Godot")
    if uses_dotnet and not result["dotnet"]:
        result["missing_tools"].append(".NET SDK")
    return result


def _executable(value: str, *, required: bool = True) -> str | None:
    validate_filesystem_path(value)
    if value.startswith("-"):
        raise ValueError("Executable must not start with an option")
    if "/" in value and not Path(value).is_absolute():
        raise ValueError("Use an absolute executable path or a name on PATH")
    found = shutil.which(value)
    if not found:
        if required:
            raise ValueError(f"Executable is not available: {value}")
        return None
    path = Path(found).absolute()
    validate_filesystem_path(str(path), must_exist=True)
    if not path.is_file() or not os.access(path, os.X_OK):
        raise ValueError("Executable must be an executable regular file")
    return str(path)


def _properties(text: str) -> dict[str, str]:
    result = {}
    for line in text.splitlines():
        key, separator, value = line.partition("=")
        if not separator or key in result:
            raise RuntimeError("Invalid user-systemd response")
        result[key] = value
    return result


def session_environment(uid: int) -> tuple[dict[str, str], str]:
    """Recover only the owned user manager's existing graphical environment."""
    runtime = Path(f"/run/user/{uid}")
    if not _owned_socket(runtime / "bus", uid):
        return {}, "Owned user bus is unavailable; log into KDE with the existing account"
    state, text = _probe(["/usr/bin/systemctl", "--user", "show", "graphical-session.target", "--property=ActiveState"], uid)
    if state != "ok" or _properties(text).get("ActiveState") != "active":
        return {}, "The existing KDE graphical session is not active; SSH alone cannot create it"
    state, text = _probe(["/usr/bin/systemctl", "--user", "show-environment"], uid)
    if state != "ok":
        return {}, "Could not read the existing user manager's graphical environment"
    selected = {}
    for line in text.splitlines():
        key = line.partition("=")[0]
        if key not in ("WAYLAND_DISPLAY", "DISPLAY", "XAUTHORITY"):
            continue
        values = shlex.split(line)
        if len(values) != 1 or key in selected:
            return {}, "User manager has an ambiguous graphical environment"
        selected[key] = values[0].partition("=")[2]
    display = selected.get("WAYLAND_DISPLAY", "")
    if not re.fullmatch(r"wayland-[A-Za-z0-9_-]{1,64}", display) or not _owned_socket(runtime / display, uid):
        return {}, "The user manager's Wayland display is missing or stale; reconnect to the existing KDE session"
    environment = {
        "XDG_RUNTIME_DIR": str(runtime), "DBUS_SESSION_BUS_ADDRESS": f"unix:path={runtime}/bus",
        "WAYLAND_DISPLAY": display, "XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "KDE",
        "NO_AT_BRIDGE": "0", "GTK_MODULES": "atk-bridge", "QT_LINUX_ACCESSIBILITY_ALWAYS_ON": "1",
    }
    if re.fullmatch(r":[0-9]+(?:\.[0-9]+)?", selected.get("DISPLAY", "")):
        environment["DISPLAY"] = selected["DISPLAY"]
    authority = selected.get("XAUTHORITY")
    if authority:
        validate_filesystem_path(authority)
        path = Path(authority)
        if path.is_absolute():
            try:
                info = path.lstat()
            except OSError:
                info = None
            if info is not None and stat.S_ISREG(info.st_mode) and info.st_uid == uid and not info.st_mode & 0o022:
                environment["XAUTHORITY"] = authority
    return environment, "Owned graphical-session target and Wayland/user-bus sockets are available; live UI readiness is unverified"


def doctor(project: str, engine: str | None = None, *, export_check: bool = False,
           session_generation: str | None = None) -> dict:
    uid = _guard()
    info = project_info(_project(project), engine)
    if export_check:
        from desktop.development_exports import inspect_exports
        info["export_check"] = inspect_exports(info)
    environment, reason = _environment(uid, session_generation)
    return {"schema_version": 1, "ok": bool(environment) and not info["missing_tools"]
            and (not export_check or info["export_check"]["ok"]),
            "prerequisites_only": True, "project": info,
            "session": {"state": "available" if environment else "deferred", "reason": reason},
            "gpu_readiness": "unverified", "input_readiness": "unverified"}


def _environment(uid: int, generation: str | None) -> tuple[dict[str, str], str]:
    if generation is None:
        return session_environment(uid)
    if not isinstance(generation, str) or not TASK_ID.fullmatch(generation):
        raise ValueError("Invalid desktop session generation")
    from desktop.debian_development import supervisor_environment
    return supervisor_environment(uid)


def _private_directory(path: Path) -> Path:
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError("Development task storage must be an owned private directory without symlinks")
    return path


def _root(*, create: bool = False) -> Path:
    home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    validate_filesystem_path(str(home), must_exist=True)
    info = home.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise ValueError("Unsafe desktop account home")
    current = home
    for name in (".local", "state", "basaltwater", "development"):
        current = current / name
        if create:
            current.mkdir(mode=0o700, exist_ok=True)
        info = current.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
            raise ValueError("Unsafe development storage ancestor")
    return _private_directory(current)


def _task(task: str) -> Path:
    if not isinstance(task, str) or not TASK_ID.fullmatch(task):
        raise ValueError("Task ID must be the 32-character identifier returned by a development launch")
    return _private_directory(_root() / task)


def _private_json(path: Path) -> dict:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError("Development records must be owned private regular files")
    value = read_json_file(str(path), max_bytes=64 * 1024)
    if not isinstance(value, dict):
        raise ValueError("Invalid development record")
    return value


def parse_task_timestamp(value: str) -> datetime:
    """Compare recorded instants in UTC, including explicit timezone offsets."""
    try:
        if not isinstance(value, str) or len(value) > 40:
            raise ValueError
        timestamp = datetime.fromisoformat(value)
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError
        return timestamp.astimezone(timezone.utc)
    except (ValueError, OverflowError) as exc:
        raise ValueError("Task timestamps must be ISO 8601 with an explicit timezone") from exc


def load_task(directory: Path) -> dict:
    _private_directory(directory)
    value = _private_json(directory / "task.json")
    if (set(value) - {"schema_version", "task", "unit", "kind", "project", "argv", "owner_uid", "created_at",
                      "invocation_id", "recipe", "control_revision", "session_generation", "playtest"}
            or type(value.get("schema_version")) is not int or value["schema_version"] != 1
            or value.get("task") != directory.name or not TASK_ID.fullmatch(directory.name)
            or value.get("unit") != f"{UNIT_PREFIX}{directory.name}.service"
            or value.get("kind") not in KINDS or type(value.get("owner_uid")) is not int
            or value["owner_uid"] != os.getuid()):
        raise ValueError("Invalid development task identity")
    parse_task_timestamp(value.get("created_at"))
    if "invocation_id" in value and (not isinstance(value["invocation_id"], str) or not TASK_ID.fullmatch(value["invocation_id"])):
        raise ValueError("Invalid development task invocation")
    if "control_revision" in value and (not isinstance(value["control_revision"], str)
                                        or not TASK_ID.fullmatch(value["control_revision"])):
        raise ValueError("Invalid development task control revision")
    if "session_generation" in value and (not isinstance(value["session_generation"], str)
                                          or not TASK_ID.fullmatch(value["session_generation"])):
        raise ValueError("Invalid development task session generation")
    if value["kind"] in CHECK_KINDS:
        recipe = value.get("recipe")
        if not isinstance(recipe, str) or not 1 <= len(recipe) <= 256 or recipe.startswith("-"):
            raise ValueError("Invalid development recipe identity")
        validate_no_control_characters(recipe, "Recipe name")
    elif "recipe" in value:
        raise ValueError("Recipe metadata belongs only to a check or comparison task")
    if value["kind"] == "playtest":
        from desktop.playtest import NAME
        name = value.get("playtest")
        if not isinstance(name, str) or not NAME.fullmatch(name):
            raise ValueError("Invalid development playtest identity")
    elif "playtest" in value:
        raise ValueError("Playtest metadata belongs only to a playtest task")
    validate_filesystem_path(value.get("project"))
    if not Path(value["project"]).is_absolute():
        raise ValueError("Task project must be absolute")
    validate_argv(value.get("argv"))
    if not Path(value["argv"][0]).is_absolute():
        raise ValueError("Task executable must be absolute")
    return value


def validate_argv(argv: list[str]) -> None:
    if not isinstance(argv, list) or not 1 <= len(argv) <= 100:
        raise ValueError("Command requires 1–100 arguments")
    for value in argv:
        if not isinstance(value, str) or len(value) > 4096:
            raise ValueError("Command arguments must be strings of at most 4096 characters")
        validate_no_control_characters(value, "Command argument")
    if len(json.dumps(argv).encode("utf-8")) > 48 * 1024:
        raise ValueError("Serialized command arguments exceed 48 KiB")


def _unit(task: dict, uid: int) -> dict[str, str]:
    state, text = _probe(["/usr/bin/systemctl", "--user", "show", task["unit"],
        "--property=LoadState,Transient,Description,InvocationID,ActiveState,SubState,MainPID,ControlGroup,TasksCurrent"], uid)
    if state != "ok":
        raise RuntimeError("Task service could not be inspected; no service was changed")
    value = _properties(text)
    if value.get("LoadState") == "not-found":
        return value
    if (value.get("Transient") != "yes" or value.get("Description") != f"Basaltwater development task {task['task']}"
            or task.get("invocation_id") and value.get("InvocationID")
            and value["InvocationID"] != task["invocation_id"]):
        raise RuntimeError("Task service identity changed; refusing to operate on it")
    return value


def _process_metadata(unit: dict[str, str]) -> dict:
    """Return a live unit snapshot, never process identities for signaling."""
    pid = unit.get("MainPID", "")
    count = unit.get("TasksCurrent", "")
    group = unit.get("ControlGroup")
    if group is not None:
        validate_no_control_characters(group, "Task control group")
        if not group.startswith("/") or len(group) > 4096:
            group = None
    return {"supervisor_pid": int(pid) if re.fullmatch(r"[0-9]{1,10}", pid) and int(pid) > 0 else None,
            "control_group": group,
            "tasks_current": int(count) if re.fullmatch(r"[0-9]{1,10}", count) else None}


def task_status(task_id: str, *, stop: bool = False, dry_run: bool = False) -> dict:
    uid = _guard()
    directory = _task(task_id)
    task = load_task(directory)
    service_error = None
    try:
        unit = _unit(task, uid)
    except (OSError, ValueError, RuntimeError) as exc:
        if stop:
            raise  # Stopping still requires verified live unit identity.
        unit, service_error = {}, str(exc)
    if stop and (dry_run or is_dry_run()):
        return {"schema_version": 1, "ok": True, "dry_run": True, "operation": "stop",
                "task": task_id, "unit": task["unit"]}
    if stop and unit.get("LoadState") != "not-found":
        _honor_agent_control()
        state, _ = _probe(["/usr/bin/systemctl", "--user", "stop", "--no-block", task["unit"]], uid)
        if state != "ok":
            raise RuntimeError("Task stop was not acknowledged; inspect its status before retrying")
        write_json_atomic(str(directory / "stop-requested.json"), {"requested_at": datetime.now(timezone.utc).isoformat()}, mode=0o600)
        unit = _unit(task, uid)
    result = {"schema_version": 1, "ok": True, "task": task_id, "unit": task["unit"],
              "kind": task["kind"], "project": task["project"], "directory": str(directory),
              "log": str(directory / "output.log"), "report": str(directory / "result.json"),
              "state": "unavailable", "returncode": None, "elapsed_seconds": None,
              "processes": None}
    if "session_generation" in task:
        result["session_generation"] = task["session_generation"]
    if task["kind"] == "check":
        result.update(recipe=task["recipe"], evidence=str(directory / "check"),
                      check_report=str(directory / "check" / "check.json"))
        if "--prepare" in task["argv"]:
            result.update(prepare_report=str(directory / "check" / "prepare.json"),
                          check_log=str(directory / "check" / "check.log"))
    elif task["kind"] == "compare":
        result.update(recipe=task["recipe"], evidence=str(directory / "comparison"),
                      comparison_report=str(directory / "comparison" / "comparison.json"))
    if task["kind"] == "playtest":
        result.update(playtest=task["playtest"], profile=str(directory / "profile"))
        if (directory / "artifacts").is_dir():
            result["artifacts"] = str(directory / "artifacts")
    elif task["kind"] == "browser":
        result.update(profile=str(directory / "profile"), downloads=str(directory / "downloads"))
    if (directory / "stop-requested.json").exists():
        request = _private_json(directory / "stop-requested.json")
        timestamp = request.get("requested_at")
        if (set(request) != {"requested_at"} or not isinstance(timestamp, str) or len(timestamp) > 40
                or datetime.fromisoformat(timestamp).tzinfo is None):
            raise ValueError("Invalid development stop request")
        result["stop_requested_at"] = timestamp
    if (directory / "started.json").exists():
        started = _private_json(directory / "started.json")
        if type(started.get("launch_pid")) is not int or started["launch_pid"] <= 0:
            raise ValueError("Invalid development launch record")
        result["launch_pid"] = started["launch_pid"]  # Initial process only; not current window identity.
    if (directory / "result.json").exists():
        completion = _private_json(directory / "result.json")
        if (set(completion) - {"returncode", "log_truncated", "started_at", "finished_at", "elapsed_seconds", "error"}
                or type(completion.get("returncode")) is not int or type(completion.get("log_truncated")) is not bool):
            raise ValueError("Invalid development completion record")
        if "elapsed_seconds" in completion:
            elapsed = completion["elapsed_seconds"]
            if (type(elapsed) not in (int, float) or elapsed < 0
                    or elapsed > sys.float_info.max or not math.isfinite(elapsed)):
                raise ValueError("Invalid development completion duration")
        result.update(completion)
        successful = completion["returncode"] == 0 and not completion.get("error")
        result["state"] = "completed" if successful else "failed"
        result["ok"] = successful
    if service_error is not None:
        result.update(recorded_state=result["state"], state="unverified", ok=False, service_error=service_error)
        return result
    if unit.get("LoadState") != "not-found":
        result["processes"] = _process_metadata(unit)
    if unit.get("ActiveState") in ("active", "activating", "deactivating") and unit.get("SubState") != "exited":
        result.update(state="stopping" if "stop_requested_at" in result or unit["ActiveState"] == "deactivating" else "running", ok=True)
    elif "stop_requested_at" in result or (directory / "stopped.json").exists():
        if (directory / "stopped.json").exists():
            _private_json(directory / "stopped.json")
        result.update(state="stopped", ok=True)
    elif unit.get("ActiveState") == "failed":
        result.update(state="failed", ok=False)
    elif result["state"] == "unavailable":
        result.update(ok=False, reason="No completion evidence or running task service; the session may have ended")
    return result


def _honor_agent_control(revision: str | None = None) -> str:
    from desktop import control_state
    current = control_state.status()
    control_state.require_enabled({"control_revision": revision if revision is not None else current["control_revision"]})
    return current["control_revision"]


def _read_log(path: Path) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError("Development logs must be owned private regular files")
        if info.st_size > LOG_LIMIT:
            raise ValueError("Development log exceeds its retained size limit")
        return stream.read(info.st_size)


def task_logs(task_id: str, *, tail: int = 30, worker: bool = False) -> dict:
    if type(tail) is not int or not 1 <= tail <= 10000:
        raise ValueError("Log tail must be from 1 through 10000 lines")
    _guard()
    directory = _task(task_id)
    task = load_task(directory)
    path = directory / "output.log"
    body = None
    if task["kind"] == "check" and not worker:
        try:
            check_log = _private_directory(directory / "check") / "check.log"
            body = _read_log(check_log)
            path = check_log
        except FileNotFoundError:
            pass  # The check may still be in preflight.
    if body is None:
        try:
            body = _read_log(path)
        except FileNotFoundError:
            return {"schema_version": 1, "ok": False, "task": task_id, "log": str(path),
                    "error": "No output log is available for this task yet"}
    # Count a trailing newline as part of the last line, including blank lines.
    lines = tail + body.endswith(b"\n")
    excerpt = b"\n".join(body.rsplit(b"\n", lines)[-lines:])
    return {"schema_version": 1, "ok": True, "task": task_id, "log": str(path),
            "output": excerpt.decode("utf-8", errors="replace")}


def wait_task(task_id: str, *, timeout: int = 600) -> dict:
    """Observe one task to verified completion without changing its lifecycle."""
    if type(timeout) is not int or not 1 <= timeout <= 3600:
        raise ValueError("Wait timeout must be from 1 through 3600 seconds")
    deadline = time.monotonic() + timeout
    while True:
        result = task_status(task_id)
        state = result["state"]
        if state not in ("running", "stopping"):
            successful = state == "completed" and result["ok"] and result["returncode"] == 0
            return {**result, "ok": successful, "wait_timed_out": False}
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return {**result, "ok": False, "wait_timed_out": True,
                    "message": f"Wait deadline reached after {timeout} seconds. Task {task_id} is still {state}."}
        time.sleep(min(0.5, remaining))


def list_tasks(project: str | None = None, *, since: str | None = None, until: str | None = None) -> dict:
    lower = parse_task_timestamp(since) if since is not None else None
    upper = parse_task_timestamp(until) if until is not None else None
    if lower is not None and upper is not None and lower > upper:
        raise ValueError("Task creation-time lower bound must not follow the upper bound")
    _guard()
    selected = str(_project(project)) if project is not None else None
    result = {"schema_version": 1, "ok": True, "tasks": [], "truncated": False,
              "scan_truncated": False, "matches_truncated": False,
              "filters": {"project": selected, "since": lower.isoformat() if lower is not None else None,
                          "until": upper.isoformat() if upper is not None else None}}
    try:
        root = _root()
    except FileNotFoundError:
        return result
    records = []
    truncated = False
    for index, directory in enumerate(root.iterdir()):
        if index >= 500:
            truncated = True
            break
        if not TASK_ID.fullmatch(directory.name):
            continue
        task = load_task(directory)
        created = parse_task_timestamp(task["created_at"])
        if ((selected is None or task["project"] == selected)
                and (lower is None or lower <= created) and (upper is None or created <= upper)):
            records.append({"task": task["task"], "kind": task["kind"], "project": task["project"],
                "created_at": task["created_at"], "directory": str(directory), "state": "recorded"})
    records.sort(key=lambda value: (parse_task_timestamp(value["created_at"]), value["task"]), reverse=True)
    return {**result, "tasks": records[:50], "truncated": truncated or len(records) > 50,
            "scan_truncated": truncated, "matches_truncated": len(records) > 50}


def cleanup_browser(task_id: str, *, include_downloads: bool = False, dry_run: bool = False) -> dict:
    uid = _guard()
    directory = _task(task_id)
    task = load_task(directory)
    if task["kind"] != "browser":
        raise ValueError("Profile cleanup requires a browser development task")
    status = task_status(task_id)
    if status["state"] not in ("completed", "failed", "stopped") or (
        status["returncode"] is None and status["state"] != "stopped"
    ):
        raise RuntimeError("Browser task exit is unverified; inspect task status before cleanup")
    unit = _unit(task, uid)
    if unit.get("LoadState") != "not-found" and (
        unit.get("ActiveState") not in ("inactive", "failed")
        or unit.get("ControlGroup") or unit.get("MainPID") != "0"
    ):
        raise RuntimeError("Browser task service may still have processes; wait before cleanup")
    names = ("profile", "downloads") if include_downloads else ("profile",)
    paths = [_private_directory(directory / name) for name in names if os.path.lexists(directory / name)]
    preview = dry_run or is_dry_run()
    if not preview:
        for path in paths:
            shutil.rmtree(path)
    return {"schema_version": 1, "ok": True, "task": task_id, "dry_run": preview,
            "paths": [str(path) for path in paths]}


def launch(project: str, kind: str, *, engine: str | None = None, scene: str | None = None,
           argv: list[str] | None = None, dry_run: bool = False, headless: bool = False,
           quit_after: int | None = None, rendering_method: str | None = None,
           script: str | None = None, manager: str | None = None, recipe: str | None = None,
           settings: str | None = None, timeout: int = 600, review_manifest: str | None = None,
           verify_evidence: bool = False, require_artifact_kind: list[str] | None = None,
           prepare: bool = False, asset_bundle: str | None = None, before: str | None = None, after: str | None = None,
           session_generation: str | None = None, control_revision: str | None = None,
           playtest: str | None = None, attach_playtest: Callable[[dict], str] | None = None,
           browser: str | None = None, url: str | None = None) -> dict:
    uid = _guard()
    if kind not in KINDS:
        raise ValueError("Unknown development task kind")
    path = _project(project)
    arguments = list(argv or [])
    # The node parser already consumes its separator; remaining '--' is literal.
    if kind != "node" and arguments[:1] == ["--"]:
        arguments = arguments[1:]
    if kind in ("import", *CHECK_KINDS) and arguments:
        raise ValueError("Import, check and comparison tasks do not accept extra command arguments")
    if kind == "playtest" and arguments:
        raise ValueError("Playtest arguments must be declared in the project")
    if kind != "browser" and (browser is not None or url is not None):
        raise ValueError("Browser options require a browser task")
    if kind not in CHECK_KINDS and review_manifest is not None:
        raise ValueError("--review-manifest requires a check task")
    if kind not in CHECK_KINDS and (verify_evidence or require_artifact_kind):
        raise ValueError("Evidence verification options require a check task")
    if kind not in CHECK_KINDS and (prepare or asset_bundle is not None):
        raise ValueError("Preparation options require a check or comparison task")
    if kind == "compare" and (not before or not after):
        raise ValueError("A revision comparison requires --before and --after")
    if kind != "compare" and (before is not None or after is not None):
        raise ValueError("--before and --after require a comparison task")
    if kind not in ("editor", "run") and (headless or quit_after is not None or rendering_method is not None):
        raise ValueError("Godot run options require editor or run")
    if quit_after is not None and (type(quit_after) is not int or not 1 <= quit_after <= 1000000):
        raise ValueError("Godot quit-after must be from 1 through 1000000 iterations")
    if rendering_method is not None and rendering_method not in ("forward_plus", "mobile", "gl_compatibility"):
        raise ValueError("Unknown Godot rendering method")
    if kind == "playtest":
        from desktop.playtest import load_plan, seed_profile
        arguments, seed = load_plan(path, playtest)
    elif kind == "browser":
        if arguments or browser not in BROWSERS:
            raise ValueError("Select a supported native browser without extra arguments")
        validate_browser_url(url)
        executable = _executable(browser, required=browser != "firefox")
        if executable is None:
            executable = _executable("firefox-esr")
        arguments = browser_command(browser, executable, url, "<task>/profile")
    elif kind == "node":
        from desktop.development_workflows import node_command
        arguments = node_command(path, script, manager, arguments)
    elif kind in CHECK_KINDS:
        from desktop.development_workflows import check_command
        arguments = check_command(path, recipe, settings, timeout, review_manifest=review_manifest,
                                  verify_evidence=verify_evidence, require_artifact_kind=require_artifact_kind,
                                  prepare=prepare, asset_bundle=asset_bundle,
                                  revisions=(before, after) if kind == "compare" else None)
    elif kind == "exec":
        validate_argv(arguments)
        arguments[0] = _executable(arguments[0])
    else:
        info = project_info(path, engine)
        if info["kind"] != "godot":
            raise ValueError("Godot editor/run/import requires project.godot in the chosen project")
        if info["missing_tools"]:
            raise ValueError("Missing project tools: " + ", ".join(info["missing_tools"]))
        user_arguments = arguments
        arguments = [info["engine"], "--path", str(path)]
        if headless or kind == "import":
            arguments.append("--headless")
        if quit_after is not None:
            arguments.extend(["--quit-after", str(quit_after)])
        if rendering_method is not None:
            arguments.extend(["--rendering-method", rendering_method])
        if kind == "import":
            arguments.append("--import")
        elif kind == "editor":
            arguments.append("--editor")
        elif scene is not None:
            validate_filesystem_path(scene)
            selected = (path / scene.removeprefix("res://")).resolve()
            if not selected.is_relative_to(path) or not selected.is_file() or selected.suffix not in (".tscn", ".scn"):
                raise ValueError("Scene must be an existing .tscn/.scn inside the chosen project")
            arguments.append(str(selected))
        if user_arguments:
            arguments.extend(["--", *user_arguments])
    validate_argv(arguments)
    environment, reason = _environment(uid, session_generation)
    if not environment:
        raise RuntimeError(reason)
    # Retain the caller's explicit project runtime selection without importing
    # its provider credentials or display/bus overrides into the user manager.
    environment["PATH"] = os.environ.get("PATH", "/usr/bin:/bin")
    validate_no_control_characters(environment["PATH"], "PATH")
    if os.environ.get("NVM_DIR"):
        environment["NVM_DIR"] = str(_project(os.environ["NVM_DIR"]))
    if kind == "node":
        environment["COREPACK_ENABLE_NETWORK"] = "0"
    if dry_run or is_dry_run():
        result = {"schema_version": 1, "ok": True, "dry_run": True, "kind": kind,
                  "project": str(path), "argv": arguments,
                  "session": "existing-xrdp" if session_generation else "existing-kde-wayland"}
        if kind == "playtest":
            seed_profile(path, seed)
            result.update(playtest=playtest, seed=seed, active_recorder_required=True)
        elif kind == "browser":
            result.update(profile="<task>/profile", downloads="<task>/downloads")
        return result
    if kind == "playtest" and attach_playtest is None:
        raise ValueError("An active desktop evidence recorder is required for playtest launch")
    revision = _honor_agent_control(control_revision)
    directory = _root(create=True) / secrets.token_hex(16)
    if kind in CHECK_KINDS:
        arguments.extend(["--output", str(directory / ("check" if kind == "check" else "comparison"))])
        validate_argv(arguments)
    directory.mkdir(mode=0o700)  # A collision must never replace an existing task.
    _private_directory(directory)
    if kind == "browser":
        try:
            arguments = browser_command(browser, executable, url, str(directory / "profile"))
            validate_argv(arguments)
            create_browser_profile(browser, directory)
        except (OSError, ValueError):
            shutil.rmtree(directory)
            raise
    task = {"schema_version": 1, "task": directory.name, "unit": f"{UNIT_PREFIX}{directory.name}.service",
            "kind": kind, "project": str(path), "argv": arguments, "owner_uid": uid,
            "created_at": datetime.now(timezone.utc).isoformat()}
    task["control_revision"] = revision
    if session_generation is not None:
        task["session_generation"] = session_generation
    if kind in CHECK_KINDS:
        task["recipe"] = recipe
    if kind == "playtest":
        task["playtest"] = playtest
    write_json_atomic(str(directory / "task.json"), task, mode=0o600)
    browser_receipt = (
        {"profile": str(directory / "profile"), "downloads": str(directory / "downloads")}
        if kind == "browser" else {}
    )
    playtest_receipt = {}
    if kind == "playtest":
        profile = directory / "profile"
        playtest_receipt = {"playtest": playtest, "profile": str(profile)}
        try:
            seed_profile(path, seed, profile)
            playtest_receipt["recording"] = attach_playtest(task)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            return {**playtest_receipt, "schema_version": 1, "ok": False, "task": directory.name,
                    "directory": str(directory), "state": "not-launched", "error": str(exc)}
    unset = ["PYTHONPATH", "PYTHONHOME", "AT_SPI_BUS_ADDRESS"]
    unset.extend(name for name in ("DISPLAY", "XAUTHORITY", "WAYLAND_DISPLAY", "XDG_CURRENT_DESKTOP") if name not in environment)
    command = ["/usr/bin/systemd-run", "--user", "--quiet", f"--unit={task['unit']}",
        f"--description=Basaltwater development task {directory.name}", "--service-type=exec",
        "--expand-environment=no", "--property=ExitType=cgroup",
        "--property=KillMode=control-group", "--property=TimeoutStopSec=10s",
        "--property=StandardOutput=null", "--property=StandardError=null",
        "--property=UnsetEnvironment=" + " ".join(unset)]
    if session_generation is None:
        command.extend(["--property=PartOf=graphical-session.target", "--property=After=graphical-session.target"])
    # systemd-run already escapes percent specifiers in environment and argv.
    command.extend(f"--setenv={name}={value}" for name, value in environment.items())
    command.extend(["--", sys.executable,
        str(Path(__file__).with_name("development_worker.py")), str(directory)])
    state, _ = _probe(command, uid)
    if state != "ok":
        reason = "User-systemd launch was not acknowledged"
    else:
        try:
            unit = _unit(task, uid)
            if re.fullmatch(r"[0-9a-f]{32}", unit.get("InvocationID", "")):
                task["invocation_id"] = unit["InvocationID"]
                write_json_atomic(str(directory / "task.json"), task, mode=0o600)
            return {**task_status(directory.name), **playtest_receipt}
        except (OSError, ValueError, RuntimeError) as exc:
            reason = f"Task was queued but its state could not be verified: {exc}"
    return {**playtest_receipt, **browser_receipt, "schema_version": 1, "ok": False, "task": directory.name, "unit": task["unit"],
            "directory": str(directory), "log": str(directory / "output.log"), "state": "launch-unverified",
            "error": f"{reason}; inspect this task's status before retrying"}


def run_development_command(args: argparse.Namespace) -> int:
    action = args.development_command
    try:
        if not getattr(args, "native", False) and action not in ("list", "status", "stop", "wait", "logs", "cleanup-browser"):
            from desktop.debian_development import request_task
            result = request_task(args)
        elif action == "doctor":
            result = doctor(args.project, args.engine, export_check=getattr(args, "export_check", False))
        elif action == "list":
            result = list_tasks(args.project, since=getattr(args, "since", None), until=getattr(args, "until", None))
        elif action in ("status", "stop"):
            result = task_status(args.task, stop=action == "stop", dry_run=getattr(args, "dry_run", False))
        elif action == "wait":
            result = wait_task(args.task, timeout=args.timeout)
        elif action == "logs":
            result = task_logs(args.task, tail=args.tail, worker=args.worker)
        elif action == "cleanup-browser":
            result = cleanup_browser(args.task, include_downloads=args.include_downloads, dry_run=args.dry_run)
        elif action == "playtest":
            from desktop.native_session import playtest_attachment
            dry_run = args.dry_run or is_dry_run()
            result = launch(args.project, action, playtest=args.playtest, dry_run=dry_run,
                            attach_playtest=None if dry_run else playtest_attachment())
        else:
            result = launch(args.project, action, engine=getattr(args, "engine", None),
                scene=getattr(args, "scene", None), argv=getattr(args, "argv", None), dry_run=args.dry_run,
                headless=getattr(args, "headless", False), quit_after=getattr(args, "quit_after", None),
                rendering_method=getattr(args, "rendering_method", None), script=getattr(args, "script", None),
                manager=getattr(args, "manager", None), recipe=getattr(args, "recipe", None),
                settings=getattr(args, "settings", None), timeout=getattr(args, "timeout", 600),
                review_manifest=getattr(args, "review_manifest", None),
                verify_evidence=getattr(args, "verify_evidence", False),
                require_artifact_kind=getattr(args, "require_artifact_kind", None),
                prepare=getattr(args, "prepare", False), asset_bundle=getattr(args, "asset_bundle", None),
                before=getattr(args, "before", None), after=getattr(args, "after", None),
                browser=getattr(args, "browser", None), url=getattr(args, "url", None))
    except DesktopRequestError as exc:
        result = {**exc.receipt, "ok": False}
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        result = {"ok": False, "error": str(exc)}
    if action == "logs" and not args.json:
        if result["ok"]:
            sys.stdout.write(result["output"])
        else:
            print(result["error"], file=sys.stderr)
        return 0 if result["ok"] else 1
    print(json.dumps(result, indent=2))
    if action == "wait" and result.get("wait_timed_out"):
        return 124
    return 0 if result["ok"] else 1
