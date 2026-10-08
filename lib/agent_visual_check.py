"""Explicit project rendering checks with private, repeatable environment evidence."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import os
import platform
import shutil
import subprocess
import time
import uuid

from lib.agent_environment import inspect_environment
from lib.agent_visuals import _directory, _settings
from lib.atomic_io import read_json_file, write_json_atomic
from lib.remote_utils import is_dry_run, run
from lib.streamed_process import run_streamed
from lib.validation import validate_filesystem_path


GRAPHICS_ENVIRONMENT = (
    "XDG_SESSION_TYPE", "DISPLAY", "WAYLAND_DISPLAY", "SDL_VIDEODRIVER",
    "SDL_RENDER_DRIVER", "LIBGL_ALWAYS_SOFTWARE", "GALLIUM_DRIVER",
    "MESA_LOADER_DRIVER_OVERRIDE", "DRI_PRIME", "QT_QPA_PLATFORM",
)
GRAPHICS_PACKAGES = (
    "mesa", "libglvnd", "vulkan-intel", "vulkan-radeon", "nvidia-utils",
    "sdl3", "sdl3_image", "mesa-utils", "xorg-server-xvfb", "xorg-xauth",
)
LOG_LIMIT = 16 * 1024 * 1024


def host_context(environment: dict[str, str]) -> dict:
    """Record bounded host facts without launching renderers or tool shims."""
    context = {
        "system": platform.system(), "kernel": platform.release(),
        "architecture": platform.machine(),
        "graphics_environment": {key: environment[key] for key in GRAPHICS_ENVIRONMENT if key in environment},
        "graphics_packages": {}, "package_observation": "unavailable",
    }
    try:
        release = platform.freedesktop_os_release()
        context["distribution"] = {key: release[key] for key in ("ID", "VERSION_ID") if key in release}
    except OSError:
        context["distribution"] = {}
    pacman = shutil.which("pacman")
    if pacman:
        try:
            result = run(
                [pacman, "-Q", *GRAPHICS_PACKAGES], check=False, capture_output=True,
                timeout=10, input_data="",
            )
            # Missing optional packages make pacman return nonzero. Retain only
            # installed packages from this fixed allowlist, never a full inventory.
            for line in (result.stdout or "").splitlines():
                name, separator, version = line.partition(" ")
                if name in GRAPHICS_PACKAGES and separator and 0 < len(version) <= 256:
                    context["graphics_packages"][name] = version
            context["package_observation"] = "observed" if result.returncode == 0 else "partial"
        except (OSError, RuntimeError, subprocess.SubprocessError):
            pass  # Package metadata is optional; the project check owns its gate.
    return context


def check_recipe(args: argparse.Namespace) -> dict:
    """Execute exactly one reviewed declaration; its exit status defines success."""
    if type(args.timeout) is not int or not 1 <= args.timeout <= 3600:
        raise ValueError("Visual check timeout must be from 1 through 3600 seconds")
    if is_dry_run():
        raise ValueError("A visual check requires execution; dry-run cannot verify rendering")
    settings = _settings(args.settings)
    manifest = inspect_environment(args.repository)
    recipe = manifest["recipes"].get(args.recipe)
    if recipe is None:
        raise ValueError(f"Declare recipe {args.recipe!r} in basaltwater-agent.json before running it")
    if recipe["missing_tools"]:
        raise ValueError("Missing recipe tools: " + ", ".join(recipe["missing_tools"]))
    cwd = os.path.normpath(os.path.join(manifest["workspace"]["repository"], recipe["directory"]))
    validate_filesystem_path(cwd, must_exist=True)
    if not os.path.isdir(cwd):
        raise ValueError("Recipe working directory must be a directory")
    directory = _directory(args.output, "check-" + uuid.uuid4().hex[:12])
    report_path = os.path.join(directory, "check.json")
    log_path = os.path.join(directory, "check.log")
    environment_path = os.path.join(directory, "environment.json")
    settings_path = os.path.join(directory, "settings.json")
    environment = os.environ.copy()
    # Project scripts may use these paths; literal recipe argv is never expanded.
    environment["BASALTWATER_VISUAL_EVIDENCE"] = directory
    environment["BASALTWATER_VISUAL_SETTINGS"] = settings_path
    write_json_atomic(settings_path, settings, mode=0o400, indent=None)
    report = {
        "schema_version": 1, "ok": False, "status": "running", "recipe": args.recipe,
        "description": recipe["description"], "command": recipe["argv"], "cwd": cwd,
        "directory": directory, "report": report_path, "log": log_path,
        "environment": environment_path, "settings": settings_path,
        "started_at": datetime.now(timezone.utc).isoformat(), "timeout": args.timeout,
        "returncode": None, "verification_scope": "project-recipe-exit-status",
        "gpu_readiness": "unverified", "ui_readiness": "unverified",
        "log_limit_bytes": LOG_LIMIT, "log_truncated": False,
    }
    write_json_atomic(report_path, report)
    started = time.monotonic()
    try:
        snapshot = {"manifest": manifest, "host": host_context(environment)}
        write_json_atomic(environment_path, snapshot)
        descriptor = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb", buffering=0) as log:
            written = 0

            def record_output(chunk: str) -> None:
                nonlocal written
                encoded = chunk.encode("utf-8", errors="replace")
                retained = encoded[:max(0, LOG_LIMIT - written)]
                log.write(retained)
                written += len(retained)
                if len(retained) < len(encoded):
                    report["log_truncated"] = True

            returncode = run_streamed(
                recipe["argv"], cwd=cwd, env=environment, timeout=args.timeout,
                on_output=record_output,
            )
        report["returncode"] = returncode
        if returncode != 0:
            raise RuntimeError(f"Project recipe exited with status {returncode}; inspect {log_path}")
        if read_json_file(settings_path, max_bytes=512 * 1024) != settings:
            raise RuntimeError("Project recipe changed the recorded settings; refusing inconsistent evidence")
        report.update(ok=True, status="passed")
    except TimeoutError:
        report.update(status="timed-out", error=f"Project recipe timed out after {args.timeout} seconds; inspect {log_path}")
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        report.update(status="failed", error=str(exc))
    except KeyboardInterrupt:
        report.update(status="interrupted", error="Project visual check interrupted")
    report["elapsed_seconds"] = round(time.monotonic() - started, 3)
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    write_json_atomic(report_path, report)
    return report
