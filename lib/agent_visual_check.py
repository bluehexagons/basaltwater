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
from lib.agent_graphics import graphics_environment
from lib.agent_reservations import termination_cleanup
from lib.agent_visuals import _directory, _settings, _settings_unchanged
from lib.atomic_io import read_json_file, write_json_atomic
from lib.remote_utils import is_dry_run, run
from lib.streamed_process import run_streamed
from lib.validation import validate_filesystem_path


ARCH_GRAPHICS_PACKAGES = (
    "mesa", "libglvnd", "vulkan-intel", "vulkan-radeon", "nvidia-utils",
    "sdl3", "sdl3_image", "mesa-utils", "xorg-server-xvfb", "xorg-xauth",
)
DEBIAN_GRAPHICS_PACKAGES = (
    "libgl1-mesa-dri", "libegl-mesa0", "libglx-mesa0", "libglvnd0",
    "libgl1", "libegl1", "mesa-vulkan-drivers", "libvulkan1",
    "nvidia-driver", "libnvidia-glcore", "libsdl2-2.0-0", "libsdl2-image-2.0-0",
    "libsdl3-0", "libsdl3-image0", "mesa-utils", "xvfb", "xauth",
)
LOG_LIMIT = 16 * 1024 * 1024


def host_context(environment: dict[str, str]) -> dict:
    """Record bounded host facts without launching renderers or tool shims."""
    context = {
        "system": platform.system(), "kernel": platform.release(),
        "architecture": platform.machine(),
        "graphics_environment": graphics_environment(environment),
        "graphics_packages": {}, "package_observation": "unavailable",
    }
    try:
        release = platform.freedesktop_os_release()
        context["distribution"] = {key: release[key] for key in ("ID", "VERSION_ID") if key in release}
    except OSError:
        release = {}
        context["distribution"] = {}
    families = {release.get("ID", ""), *release.get("ID_LIKE", "").split()}
    command = None
    if families & {"debian", "ubuntu"}:
        manager = shutil.which("dpkg-query")
        packages = DEBIAN_GRAPHICS_PACKAGES
        if manager:
            command = [manager, "--show", "--showformat=${binary:Package}\t${Version}\t${db:Status-Status}\n", *packages]
    elif families & {"arch", "cachyos"}:
        manager = shutil.which("pacman")
        packages = ARCH_GRAPHICS_PACKAGES
        if manager:
            command = [manager, "-Q", *packages]
    if command:
        try:
            result = run(
                command, check=False, capture_output=True,
                timeout=10, input_data="",
                env={**os.environ, "LC_ALL": "C"},
            )
            if result.returncode not in (0, 1):
                return context
            # Missing optional packages return 1. Dpkg also knows removed or
            # unpacked packages, so require installed status. Preserve multiarch
            # qualifiers so two installed architectures do not overwrite evidence.
            for line in (result.stdout or "").splitlines():
                if families & {"debian", "ubuntu"}:
                    fields = line.split("\t")
                    if len(fields) != 3 or fields[2] != "installed":
                        continue
                    name, version = fields[:2]
                else:
                    name, separator, version = line.partition(" ")
                    if not separator:
                        continue
                if name.partition(":")[0] in packages and 0 < len(version) <= 256:
                    context["graphics_packages"][name] = version
            context["package_observation"] = "observed" if result.returncode == 0 else "partial"
        except (OSError, RuntimeError, subprocess.SubprocessError):
            pass  # Package metadata is optional; the project check owns its gate.
    return context


@termination_cleanup()
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
    receipt_path = os.path.join(directory, "receipt.json")
    revision = manifest["workspace"].get("commit")
    environment["BASALTWATER_VISUAL_REVISION"] = revision or ""
    environment["BASALTWATER_VISUAL_RECEIPT"] = receipt_path
    environment["BASALTWATER_VISUAL_VERIFY"] = "1" if args.verify_evidence or args.require_artifact_kind else "0"
    write_json_atomic(settings_path, settings, mode=0o400, indent=None)
    report = {
        "schema_version": 1, "ok": False, "status": "running", "recipe": args.recipe,
        "description": recipe["description"], "command": recipe["argv"], "cwd": cwd,
        "directory": directory, "report": report_path, "log": log_path,
        "environment": environment_path, "settings": settings_path,
        "started_at": datetime.now(timezone.utc).isoformat(), "timeout": args.timeout,
        "returncode": None, "verification_scope": "project-recipe-exit-status",
        "gpu_readiness": "unverified", "ui_readiness": "unverified",
        "verification": {"status": "unverified"},
        "log_limit_bytes": LOG_LIMIT, "log_truncated": False,
    }
    performance = None
    profile = getattr(args, "power_profile", None)
    if getattr(args, "performance_session", False) or profile:
        performance = {"schema_version": 1, "requested_profile": profile, "before": None, "after": None,
                       "active_observation": os.path.join(directory, "performance-active.json"),
                       "scope": "point-in-time-snapshots; no peak or exclusivity assertion"}
        report["performance"] = os.path.join(directory, "performance.json")
    write_json_atomic(report_path, report)
    started = time.monotonic()
    try:
        argv = recipe["argv"]
        if performance is not None:
            from lib.agent_performance import performance_argv, performance_snapshot

            performance["before"] = performance_snapshot()
            write_json_atomic(report["performance"], performance)
            argv = performance_argv(argv, directory, args.timeout, profile)
            report["execution_command"] = argv
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

            returncode = run_streamed(argv, cwd=cwd, env=environment, timeout=args.timeout,
                                      on_output=record_output, cleanup_group_on_exit=performance is not None)
        report["returncode"] = returncode
        if returncode != 0:
            raise RuntimeError(f"Project recipe exited with status {returncode}; inspect {log_path}")
        if performance is not None:
            active = read_json_file(performance["active_observation"], max_bytes=2 * 1024 * 1024)
            if (not isinstance(active, dict) or active.get("schema_version") != 1
                    or (profile and active.get("power_profile") != profile)):
                raise ValueError("Performance session did not retain the expected active observation")
        if not _settings_unchanged(settings_path, settings):
            raise RuntimeError("Project recipe changed the recorded settings; refusing inconsistent evidence")
        if args.verify_evidence or args.require_artifact_kind:
            from lib.visual_evidence import verify_receipt

            report["verification"] = verify_receipt(receipt_path, manifest["workspace"]["repository"], revision, settings, args.require_artifact_kind)
            report["verification_scope"] = "project-recipe-and-checked-receipt"
        report.update(ok=True, status="passed")
    except TimeoutError:
        report.update(status="timed-out", error=f"Project recipe timed out after {args.timeout} seconds; inspect {log_path}")
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        report.update(status="failed", error=str(exc))
    except KeyboardInterrupt:
        report.update(status="interrupted", error="Project visual check interrupted")
    if performance is not None:
        from lib.agent_performance import performance_snapshot

        try:
            performance["after"] = performance_snapshot()
            write_json_atomic(report["performance"], performance)
        except (OSError, ValueError, RuntimeError) as exc:
            report.update(ok=False, status="failed", error=f"Performance evidence failed: {exc}")
        except KeyboardInterrupt:
            report.update(ok=False, status="interrupted", error="Performance evidence collection interrupted")
    report["elapsed_seconds"] = round(time.monotonic() - started, 3)
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    write_json_atomic(report_path, report)
    return report
