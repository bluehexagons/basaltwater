"""Project-aware development tasks in the existing CachyOS user's KDE session."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
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

from lib.atomic_io import read_json_file, write_json_atomic
from lib.cachyos import is_cachyos
from lib.cachyos_doctor import _owned_socket, _probe
from lib.remote_utils import is_dry_run
from lib.validation import validate_filesystem_path, validate_no_control_characters


LOG_LIMIT = 16 * 1024 * 1024
TASK_ID = re.compile(r"[0-9a-f]{32}")
UNIT_PREFIX = "basaltwater-development-"
KINDS = ("editor", "run", "import", "exec", "node", "check")


def add_development_parser(commands: argparse._SubParsersAction) -> None:
    parser = commands.add_parser("develop", help="Check projects and supervise native development tasks without screen control")
    actions = parser.add_subparsers(dest="development_command", required=True)
    for name in ("doctor", "editor", "run", "import", "exec"):
        action = actions.add_parser(name)
        action.add_argument("--project", required=True, help="Existing project or worktree directory")
        action.add_argument("--json", action="store_true")
        if name != "exec":
            action.add_argument("--engine", help="Godot executable name or absolute path; defaults to godot or godot-mono for C#")
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
    for name in ("node", "check"):
        action = actions.add_parser(name, help="Run a declared package script" if name == "node" else "Run a declared project rendering/test recipe in the native session")
        action.add_argument("script" if name == "node" else "recipe")
        action.add_argument("--project", required=True)
        action.add_argument("--json", action="store_true")
        action.add_argument("--dry-run", action="store_true")
        if name == "node":
            action.add_argument("--manager", choices=("npm", "pnpm", "yarn"), help="Override packageManager; defaults to npm when undeclared")
            action.add_argument("argv", nargs="*", help="Script arguments following --")
        else:
            action.add_argument("--settings", help="Existing non-secret JSON settings to record and pass to the recipe")
            action.add_argument("--timeout", type=int, default=600, help="Recipe process-group deadline in seconds (1–3600)")
    for name in ("status", "stop"):
        action = actions.add_parser(name)
        action.add_argument("task", help="Task ID returned by a development launch")
        action.add_argument("--json", action="store_true")
        if name == "stop":
            action.add_argument("--dry-run", action="store_true", help="Validate the task identity without stopping it")
    listing = actions.add_parser("list", help="List private task records; use status for current process state")
    listing.add_argument("--project", help="Filter records to an existing project/worktree")
    listing.add_argument("--json", action="store_true")


def _guard() -> int:
    uid = os.getuid()
    if uid == 0 or os.geteuid() != uid or not is_cachyos():
        raise RuntimeError("Run as the existing desktop account on CachyOS, without sudo")
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


def doctor(project: str, engine: str | None = None) -> dict:
    uid = _guard()
    info = project_info(_project(project), engine)
    environment, reason = session_environment(uid)
    return {"schema_version": 1, "ok": bool(environment) and not info["missing_tools"],
            "prerequisites_only": True, "project": info,
            "session": {"state": "available" if environment else "deferred", "reason": reason},
            "gpu_readiness": "unverified", "input_readiness": "unverified"}


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


def load_task(directory: Path) -> dict:
    _private_directory(directory)
    value = _private_json(directory / "task.json")
    if (set(value) - {"schema_version", "task", "unit", "kind", "project", "argv", "owner_uid", "created_at", "invocation_id", "recipe"}
            or type(value.get("schema_version")) is not int or value["schema_version"] != 1
            or value.get("task") != directory.name or not TASK_ID.fullmatch(directory.name)
            or value.get("unit") != f"{UNIT_PREFIX}{directory.name}.service"
            or value.get("kind") not in KINDS or type(value.get("owner_uid")) is not int
            or value["owner_uid"] != os.getuid()):
        raise ValueError("Invalid development task identity")
    timestamp = value.get("created_at")
    if not isinstance(timestamp, str) or len(timestamp) > 40 or datetime.fromisoformat(timestamp).tzinfo is None:
        raise ValueError("Invalid development task timestamp")
    if "invocation_id" in value and (not isinstance(value["invocation_id"], str) or not TASK_ID.fullmatch(value["invocation_id"])):
        raise ValueError("Invalid development task invocation")
    if value["kind"] == "check":
        recipe = value.get("recipe")
        if not isinstance(recipe, str) or not 1 <= len(recipe) <= 256 or recipe.startswith("-"):
            raise ValueError("Invalid development recipe identity")
        validate_no_control_characters(recipe, "Recipe name")
    elif "recipe" in value:
        raise ValueError("Recipe metadata belongs only to a check task")
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
        "--property=LoadState,Transient,Description,InvocationID,ActiveState,SubState"], uid)
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
        state, _ = _probe(["/usr/bin/systemctl", "--user", "stop", "--no-block", task["unit"]], uid)
        if state != "ok":
            raise RuntimeError("Task stop was not acknowledged; inspect its status before retrying")
        write_json_atomic(str(directory / "stop-requested.json"), {"requested_at": datetime.now(timezone.utc).isoformat()}, mode=0o600)
        unit = _unit(task, uid)
    result = {"schema_version": 1, "ok": True, "task": task_id, "unit": task["unit"],
              "kind": task["kind"], "project": task["project"], "directory": str(directory),
              "log": str(directory / "output.log"), "state": "unavailable", "returncode": None}
    if task["kind"] == "check":
        result.update(recipe=task["recipe"], evidence=str(directory / "check"),
                      check_report=str(directory / "check" / "check.json"))
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
        if (set(completion) - {"returncode", "log_truncated", "finished_at", "error"}
                or type(completion.get("returncode")) is not int or type(completion.get("log_truncated")) is not bool):
            raise ValueError("Invalid development completion record")
        result.update(completion)
        successful = completion["returncode"] == 0 and not completion.get("error")
        result["state"] = "completed" if successful else "failed"
        result["ok"] = successful
    if service_error is not None:
        result.update(recorded_state=result["state"], state="unverified", ok=False, service_error=service_error)
        return result
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


def _honor_human_pause() -> None:
    from desktop import native_grants, native_session
    if native_grants.metadata()["paused"]:
        raise RuntimeError("Human paused native control; wait for explicit handback before launching")
    try:
        current = native_session.request({"action": "status"})
    except (FileNotFoundError, ConnectionRefusedError):
        return
    if current.get("paused"):
        raise RuntimeError("Human paused native control; wait for explicit handback before launching")


def list_tasks(project: str | None = None) -> dict:
    _guard()
    selected = str(_project(project)) if project is not None else None
    try:
        root = _root()
    except FileNotFoundError:
        return {"schema_version": 1, "ok": True, "tasks": [], "truncated": False}
    records = []
    truncated = False
    for index, directory in enumerate(root.iterdir()):
        if index >= 500:
            truncated = True
            break
        if not TASK_ID.fullmatch(directory.name):
            continue
        task = load_task(directory)
        if selected is None or task["project"] == selected:
            records.append({"task": task["task"], "kind": task["kind"], "project": task["project"],
                "created_at": task["created_at"], "directory": str(directory), "state": "recorded"})
    records.sort(key=lambda value: value["created_at"], reverse=True)
    return {"schema_version": 1, "ok": True, "tasks": records[:50], "truncated": truncated or len(records) > 50}


def launch(project: str, kind: str, *, engine: str | None = None, scene: str | None = None,
           argv: list[str] | None = None, dry_run: bool = False, headless: bool = False,
           quit_after: int | None = None, rendering_method: str | None = None,
           script: str | None = None, manager: str | None = None, recipe: str | None = None,
           settings: str | None = None, timeout: int = 600) -> dict:
    uid = _guard()
    if kind not in KINDS:
        raise ValueError("Unknown development task kind")
    path = _project(project)
    arguments = list(argv or [])
    if arguments[:1] == ["--"]:
        arguments = arguments[1:]
    if kind in ("import", "check") and arguments:
        raise ValueError("Import and check tasks do not accept extra command arguments")
    if kind not in ("editor", "run") and (headless or quit_after is not None or rendering_method is not None):
        raise ValueError("Godot run options require editor or run")
    if quit_after is not None and (type(quit_after) is not int or not 1 <= quit_after <= 1000000):
        raise ValueError("Godot quit-after must be from 1 through 1000000 iterations")
    if rendering_method is not None and rendering_method not in ("forward_plus", "mobile", "gl_compatibility"):
        raise ValueError("Unknown Godot rendering method")
    if kind == "node":
        from desktop.development_workflows import node_command
        arguments = node_command(path, script, manager, arguments)
    elif kind == "check":
        from desktop.development_workflows import check_command
        arguments = check_command(path, recipe, settings, timeout)
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
    environment, reason = session_environment(uid)
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
        return {"schema_version": 1, "ok": True, "dry_run": True, "kind": kind,
                "project": str(path), "argv": arguments, "session": "existing-kde-wayland"}
    _honor_human_pause()
    directory = _root(create=True) / secrets.token_hex(16)
    directory.mkdir(mode=0o700)  # A collision must never replace an existing task.
    _private_directory(directory)
    if kind == "check":
        arguments.extend(["--output", str(directory / "check")])
        validate_argv(arguments)
    task = {"schema_version": 1, "task": directory.name, "unit": f"{UNIT_PREFIX}{directory.name}.service",
            "kind": kind, "project": str(path), "argv": arguments, "owner_uid": uid,
            "created_at": datetime.now(timezone.utc).isoformat()}
    if kind == "check":
        task["recipe"] = recipe
    write_json_atomic(str(directory / "task.json"), task, mode=0o600)
    unset = ["PYTHONPATH", "PYTHONHOME", "AT_SPI_BUS_ADDRESS"]
    unset.extend(name for name in ("DISPLAY", "XAUTHORITY") if name not in environment)
    command = ["/usr/bin/systemd-run", "--user", "--quiet", f"--unit={task['unit']}",
        f"--description=Basaltwater development task {directory.name}", "--service-type=exec",
        "--expand-environment=no", "--property=ExitType=cgroup",
        "--property=KillMode=control-group", "--property=TimeoutStopSec=10s",
        "--property=PartOf=graphical-session.target", "--property=After=graphical-session.target",
        "--property=StandardOutput=null", "--property=StandardError=null",
        "--property=UnsetEnvironment=" + " ".join(unset)]
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
            return task_status(directory.name)
        except (OSError, ValueError, RuntimeError) as exc:
            reason = f"Task was queued but its state could not be verified: {exc}"
    return {"schema_version": 1, "ok": False, "task": directory.name, "unit": task["unit"],
            "directory": str(directory), "log": str(directory / "output.log"), "state": "launch-unverified",
            "error": f"{reason}; inspect this task's status before retrying"}


def run_development_command(args: argparse.Namespace) -> int:
    try:
        if not getattr(args, "native", False):
            raise ValueError("Development tasks require 'desktop --native develop'")
        action = args.development_command
        if action == "doctor":
            result = doctor(args.project, args.engine)
        elif action == "list":
            result = list_tasks(args.project)
        elif action in ("status", "stop"):
            result = task_status(args.task, stop=action == "stop", dry_run=getattr(args, "dry_run", False))
        else:
            result = launch(args.project, action, engine=getattr(args, "engine", None),
                scene=getattr(args, "scene", None), argv=getattr(args, "argv", None), dry_run=args.dry_run,
                headless=getattr(args, "headless", False), quit_after=getattr(args, "quit_after", None),
                rendering_method=getattr(args, "rendering_method", None), script=getattr(args, "script", None),
                manager=getattr(args, "manager", None), recipe=getattr(args, "recipe", None),
                settings=getattr(args, "settings", None), timeout=getattr(args, "timeout", 600))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        result = {"ok": False, "error": str(exc)}
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1
