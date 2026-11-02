"""Deliberate, bounded Blender CPU rendering checks with private evidence."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import time

from desktop import blender_smoke_scene
from lib import agent_workspace
from lib.agent_visuals import _image
from lib.atomic_io import read_json_file, write_json_atomic
from lib.remote_utils import is_dry_run, run
from lib.validation import validate_filesystem_path, validate_positive_integer


def add_blender_parser(commands: argparse._SubParsersAction) -> None:
    parser = commands.add_parser("blender", help="Check Blender rendering with private evidence")
    actions = parser.add_subparsers(dest="blender_command", required=True)
    smoke = actions.add_parser("smoke", help="Render a small built-in Cycles CPU scene without a desktop")
    smoke.add_argument("--output", help="New artifact directory with an existing parent; default: private managed state")
    smoke.add_argument("--timeout", type=validate_positive_integer, default=120, help="Render timeout in seconds, 1–600 (default: 120)")
    smoke.add_argument("--json", action="store_true")
    inspect = actions.add_parser("inspect", help="Inspect evaluated asset geometry without changing the source blend")
    inspect.add_argument("source", help="Existing .blend, OBJ, glTF/GLB or JSON assembly")
    inspect.add_argument("--dependency", action="append", default=[], help="Companion file to hash before/after inspection; repeat for buffers, materials and textures")
    inspect.add_argument("--object", action="append", default=[], dest="objects", help="Exact scene mesh name; repeat to restrict inspection")
    inspect.add_argument("--intersect", nargs=2, action="append", default=[], metavar=("OBJECT", "OBJECT"), help="Check a designated object pair for surface overlap")
    inspect.add_argument("--allow-intersection", nargs=2, action="append", default=[], metavar=("OBJECT", "OBJECT"), help="Record an intentional overlap for a designated pair")
    inspect.add_argument("--support", nargs=2, action="append", default=[], metavar=("OBJECT", "SUPPORT"), help="Observe downward support gaps at an object's lowest vertices")
    inspect.add_argument("--support-tolerance", type=float, default=0.001, help="World-space contact tolerance (default 0.001); vertical axis is Z")
    inspect.add_argument("--render-views", action="store_true", help="Render front/rear/underside CPU diagnostic views")
    inspect.add_argument("--size", type=validate_positive_integer, default=512, help="Square diagnostic resolution, 64–2048 (default 512)")
    inspect.add_argument("--output", help="New private evidence directory")
    inspect.add_argument("--timeout", type=validate_positive_integer, default=300, help="Process deadline, 1–3600 seconds")
    inspect.add_argument("--json", action="store_true")


def _directory(output: str | None) -> str:
    if output is not None:
        validate_filesystem_path(output)
        destination = os.path.abspath(os.path.expanduser(output))
        validate_filesystem_path(os.path.dirname(destination), must_exist=True, check_writable=True)
        os.mkdir(destination, mode=0o700)  # Refuse existing files, directories and symlinks.
        return destination
    home = agent_workspace._effective_home()
    parent = agent_workspace._managed_root(
        home, os.path.join(home, ".local/state/basaltwater/blender"), create=True,
    )
    return tempfile.mkdtemp(prefix="smoke-", dir=parent)


def _environment(directory: str) -> dict[str, str]:
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith("BLENDER_")
        and key not in {"DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "PYTHONPATH", "PYTHONHOME"}
    }
    for key, name in (
        ("BLENDER_USER_CONFIG", "config"), ("BLENDER_USER_SCRIPTS", "scripts"),
        ("BLENDER_USER_DATAFILES", "datafiles"), ("XDG_CACHE_HOME", "cache"),
        ("TMPDIR", "tmp"),
    ):
        path = os.path.join(directory, name)
        os.mkdir(path, mode=0o700)
        environment[key] = path
    return environment


def _verify_artifacts(directory: str) -> dict:
    settings = read_json_file(os.path.join(directory, "settings.json"), max_bytes=32 * 1024)
    expected = {
        "engine": "CYCLES", "device": "CPU", "frame": 1, "width": 128,
        "height": 128, "samples": 8, "seed": 0, "threads": 2,
        "denoising": False, "render_completed": True,
    }
    if not isinstance(settings, dict) or any(settings.get(key) != value for key, value in expected.items()):
        raise ValueError("Blender did not complete the expected CPU render")
    if not isinstance(settings.get("blender_version"), str) or not settings["blender_version"]:
        raise ValueError("Blender version is missing from render settings")
    image = _image(os.path.join(directory, "render.png"), {})["metadata"]
    if (image["width"], image["height"]) != (128, 128):
        raise ValueError("Rendered PNG has unexpected dimensions")
    descriptor = os.open(os.path.join(directory, "scene.blend"), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as scene:
        if not stat.S_ISREG(os.fstat(scene.fileno()).st_mode) or scene.read(7) != b"BLENDER":
            raise ValueError("Blender did not save a regular blend scene")
    return {"settings": settings, "image": image}


def smoke_blender(output: str | None = None, *, timeout: int = 120) -> dict:
    """Run only the bundled fixture, keeping logs and a report even on failure."""
    if type(timeout) is not int or not 1 <= timeout <= 600:
        raise ValueError("Blender smoke timeout must be between 1 and 600 seconds")
    if is_dry_run():
        raise ValueError("A Blender smoke check requires execution; dry-run cannot verify rendering")
    executable = shutil.which("blender")
    if executable is None:
        raise RuntimeError("Blender is not on PATH; select --blender in setup")
    directory = _directory(output)
    log_path = os.path.join(directory, "blender.log")
    report_path = os.path.join(directory, "report.json")
    command = [
        executable, "--background", "--factory-startup", "--disable-autoexec",
        "--threads", "2", "--python-exit-code", "1", "--python",
        str(Path(blender_smoke_scene.__file__).resolve()), "--", directory,
    ]
    report = {
        "schema_version": 1, "ok": False, "directory": directory,
        "log": log_path, "report": report_path, "command": command,
        "started_at": datetime.now(timezone.utc).isoformat(), "timeout": timeout,
        "returncode": None, "ui_readiness": "unverified", "gpu_readiness": "unverified",
    }
    started = time.monotonic()
    try:
        environment = _environment(directory)
        descriptor = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as log:
            result = run(
                command, check=False, cwd=directory, env=environment,
                stdout=log, stderr=subprocess.STDOUT, timeout=timeout, input_data="",
            )
        report["returncode"] = result.returncode
        if result.returncode != 0:
            raise RuntimeError(f"Blender exited with status {result.returncode}; inspect {log_path}")
        report.update(_verify_artifacts(directory))
        report["scene"] = os.path.join(directory, "scene.blend")
        report["ok"] = True
    except TimeoutError:
        report["error"] = f"Blender render timed out after {timeout} seconds; inspect {log_path}"
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        report["error"] = str(exc)
    except KeyboardInterrupt:
        report["error"] = "Blender smoke check interrupted"
    report["elapsed_seconds"] = round(time.monotonic() - started, 3)
    write_json_atomic(report_path, report, mode=0o600)
    return report


def run_blender_command(args: argparse.Namespace) -> int:
    try:
        if args.blender_command == "inspect":
            from lib.agent_blender_inspect import inspect_blender

            result = inspect_blender(args)
        else:
            result = smoke_blender(args.output, timeout=args.timeout)
    except (OSError, RuntimeError, ValueError) as exc:
        result = {"ok": False, "error": str(exc)}
    if args.json:
        print(json.dumps(result, indent=2))
    elif result["ok"]:
        if args.blender_command == "inspect":
            print(f"Inspected {len(result['inspection']['objects'])} objects; visual correctness remains unverified")
        else:
            print(f"Blender {result['settings']['blender_version']}: Cycles CPU render passed ({result['elapsed_seconds']}s)")
        print(f"Evidence: {result['directory']}")
        print("UI and GPU readiness remain unverified")
    else:
        print(f"Error: {result['error']}")
        if "directory" in result:
            print(f"Evidence: {result['directory']}")
    return 0 if result["ok"] else 1
